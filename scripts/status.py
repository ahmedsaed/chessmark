#!/usr/bin/env python3
"""Everything that is running, and everything that is stuck (OPS-21).

    ./chessmark status              # the whole picture
    ./chessmark status --games      # one section
    ./chessmark status --wide       # do not truncate names

Written against a question that had no answer without four commands and a browser tab: *is the
harness healthy right now?* `docker ps` says the containers are up, which is not the same thing —
a stack can be entirely "up" while every game is paused behind a rate limit, the free allowance is
spent, and a pool has not moved a piece since yesterday.

**Colour is a judgement, not decoration.** Green means working, amber means worth a look, red means
somebody needs to do something. Anything printed in amber or red is repeated in a summary at the
bottom, so the answer to "is anything wrong" never depends on reading every line.

Read-only throughout, and every section is independently fault-tolerant: a section that cannot
reach its datastore prints what it could not read and the rest still renders. A status command that
dies because one thing is broken is useless exactly when it is needed.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from chessmark.core.budget import GlobalBudget  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.core.cooldown import KEY_PREFIX as COOLDOWN_PREFIX  # noqa: E402
from chessmark.core.halt import Halt  # noqa: E402
from chessmark.db.enums import EventType, GameStatus  # noqa: E402
from chessmark.db.models import (  # noqa: E402
    Game,
    GameEvent,
    LlmCall,
    ModelRegistry,
    Player,
    Ply,
    Tournament,
    TournamentGame,
)
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.orchestration.queue import DEFAULT_GROUP, DEFAULT_STREAM  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, AMBER = "\033[31m", "\033[32m", "\033[33m"

#: A running game whose last event is older than this has stopped moving without saying so. Above
#: the reconciler's own stale threshold, so a game it is about to rescue is not reported as stuck.
STUCK_AFTER = dt.timedelta(hours=1)

#: A paused game past this much of its 24-hour patience is closer to being abandoned than resumed.
PAUSE_WARN_FRACTION = 0.5

#: Unacked deliveries above which the workers are not keeping up.
#:
#: **Pending, not stream length.** `XLEN` counts every entry the stream still holds, acked or not,
#: because entries live until `MAXLEN` trims them at 100,000 — so it climbs all day on a perfectly
#: healthy system and says nothing about a backlog. The first version of this reported "queue is
#: deep · 3149 in the stream, 0 delivered and unacked", which was a warning about nothing.
PENDING_WARN = 25

#: When the queue takes an unacked delivery away from the consumer holding it.
RECLAIM_AFTER = dt.timedelta(minutes=15)

#: Past this a consumer is dead — **whether or not it is holding a job**.
#:
#: **Sixty seconds, because a live worker touches the server every two.** It blocks on
#: `XREADGROUP` for `block_ms=2000` and comes straight back, so a consumer that has not been seen
#: for a minute is not "quiet", it is gone. The first threshold here was two hours, which listed
#: six consumers for three running containers and left a reader counting.
#:
#: They accumulate because a worker's name is `worker-{uuid4}`, generated per **process**, so every
#: restart abandons one. Redis keeps a consumer name forever; only `XGROUP DELCONSUMER` removes it,
#: and this command is read-only.
#:
#: **The "holding nothing" qualifier was wrong.** A worker killed mid-turn — by a deploy, say —
#: leaves its delivery *pending* under a dead name, so the old rule kept it in the table and marked
#: it green. Two of those for one game read as two workers racing, which the row lock makes
#: impossible (ADR-0022): a second live worker is refused the claim, returns `in_flight` and acks
#: within milliseconds, so it can never sit on a delivery. Two pending entries for one ply always
#: means at least one holder is gone.
CONSUMER_DEAD_AFTER = dt.timedelta(seconds=60)


class Report:
    """Lines to print, and the ones that were not green."""

    def __init__(self, *, wide: bool = False) -> None:
        self.concerns: list[str] = []
        self.wide = wide

    def head(self, title: str) -> None:
        print(f"\n{BOLD}{AMBER}{title}{OFF}")

    def ok(self, label: str, detail: str = "") -> None:
        self._line(GREEN, "●", label, detail, concern=False)

    def warn(self, label: str, detail: str = "") -> None:
        self._line(AMBER, "▲", label, detail, concern=True)

    def bad(self, label: str, detail: str = "") -> None:
        self._line(RED, "✖", label, detail, concern=True)

    def _line(self, colour: str, mark: str, label: str, detail: str, *, concern: bool) -> None:
        print(f"  {colour}{mark}{OFF} {label}{(' ' + DIM + detail + OFF) if detail else ''}")
        if concern:
            self.concerns.append(f"{colour}{mark}{OFF} {label}")

    def plain(self, line: str) -> None:
        print(f"    {DIM}{line}{OFF}")

    def stat(self, label: str, value: str) -> None:
        print(f"  {DIM}{label:<11}{OFF}{value}")

    def table(
        self, headers: list[str], rows: list[list[str]], marks: list[str] | None = None
    ) -> None:
        """A left-aligned table sized to its contents.

        Written here rather than pulled in, because the one thing this file must not do is fail to
        run on a server: a status command with a dependency is one that goes missing exactly when
        somebody is trying to find out why things are missing.
        """
        if not rows:
            self.plain("none")
            return
        marks = marks or ["" for _ in rows]
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(_plain(cell)))

        print(f"    {DIM}{'  '.join(h.ljust(widths[i]) for i, h in enumerate(headers))}{OFF}")
        for mark, row in zip(marks, rows, strict=False):
            cells = [_pad(cell, widths[i]) for i, cell in enumerate(row)]
            print(f"  {mark or ' '} {'  '.join(cells)}")


def _plain(text: str) -> str:
    """The text without its colour codes, so a table's columns line up."""
    out: list[str] = []
    skipping = False
    for char in text:
        if char == "\033":
            skipping = True
        elif skipping:
            skipping = char != "m"
        else:
            out.append(char)
    return "".join(out)


def _pad(cell: str, width: int) -> str:
    return cell + " " * (width - len(_plain(cell)))


def _cut(text: str, limit: int, wide: bool) -> str:
    return text if wide or len(text) <= limit else text[: limit - 1] + "…"


def ago(at: dt.datetime | None) -> str:
    return "never" if at is None else _span(int((_now() - _aware(at)).total_seconds()))


def ahead(at: dt.datetime | None) -> str:
    if at is None:
        return "unknown"
    seconds = int((_aware(at) - _now()).total_seconds())
    return "due" if seconds <= 0 else _span(seconds)


def _span(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172_800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(at: dt.datetime) -> dt.datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=dt.UTC)


def _count(value: Any) -> str:
    return f"{int(value or 0):,}"


# ---------------------------------------------------------------------- sections


async def show_platform(report: Report, session: Any) -> None:
    """The numbers a reader wants first: how much has this thing actually done?"""
    report.head("platform")

    games = dict(
        (await session.execute(sa.select(Game.status, sa.func.count()).group_by(Game.status))).all()
    )
    total = sum(games.values())
    parts = " · ".join(f"{n} {s.value}" for s, n in sorted(games.items(), key=lambda kv: kv[0]))
    report.stat("games", f"{total} · {parts}" if total else "none yet")

    plies = await session.scalar(sa.select(sa.func.count()).select_from(Ply)) or 0
    finished = games.get(GameStatus.FINISHED, 0)
    mean = f" · {plies / finished:.1f} per finished game" if finished else ""
    report.stat("plies", f"{_count(plies)}{mean}")

    tokens, cost, calls = (
        await session.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(LlmCall.prompt_tokens + LlmCall.completion_tokens), 0),
                sa.func.coalesce(sa.func.sum(LlmCall.cost_usd), 0),
                sa.func.count(),
            )
        )
    ).one()
    report.stat("provider", f"{_count(calls)} calls · {_count(tokens)} tokens · ${cost:.4f}")

    illegal = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Player.illegal_attempts), 0))
    )
    rate = f" · {int(illegal or 0) / plies:.1%} of plies" if plies else ""
    report.stat("illegal", f"{_count(illegal)} attempts{rate}")

    enabled = await session.scalar(
        sa.select(sa.func.count()).select_from(ModelRegistry).where(ModelRegistry.enabled.is_(True))
    )
    disabled = await session.scalar(
        sa.select(sa.func.count())
        .select_from(ModelRegistry)
        .where(ModelRegistry.enabled.is_(False))
    )
    report.stat("registry", f"{_count(enabled)} enabled · {_count(disabled)} disabled")


async def show_halt(report: Report, redis: Any) -> None:
    report.head("harness")
    state = await Halt(redis).state()
    if state is None:
        report.ok("running", "no halt")
        return

    # Red rather than amber: nothing is playing, and that is worth acting on even when deliberate.
    scope = "every model" if state.scope == "all" else f"{state.scope} models"
    report.bad(f"HALTED ({state.source})", f"{state.reason} — stops {scope}")
    report.plain(f"set {ago(state.at)} ago")
    if state.until is not None:
        report.plain(f"lifts by itself in {ahead(state.until)}")
    elif state.self_clearing:
        report.plain("lifts once the account has credit again (probed every 5 minutes)")
    else:
        report.plain("set by hand; lift it with ./chessmark halt --clear")


async def show_budgets(report: Report, redis: Any) -> None:
    report.head("budgets")
    settings = get_settings()

    spend = GlobalBudget(redis, daily_limit_usd=Decimal(str(settings.global_daily_usd_budget)))
    spent = await spend.spent_today()
    if spend.limit_usd <= 0:
        report.warn("no daily spend limit set", f"${spent} spent today")
    elif await spend.tripped():
        report.bad("daily spend limit reached", f"${spent} of ${spend.limit_usd}")
    else:
        share = spent / spend.limit_usd
        line = f"${spent} of ${spend.limit_usd} today"
        (report.warn if share > Decimal("0.8") else report.ok)("spend", line)


async def show_workers(report: Report, redis: Any) -> None:
    """Who is consuming the queue, what each of them holds, and which game that is."""
    report.head("workers")
    try:
        groups = await redis.xinfo_groups(DEFAULT_STREAM)
        consumers = await redis.xinfo_consumers(DEFAULT_STREAM, DEFAULT_GROUP)
        length = await redis.xlen(DEFAULT_STREAM)
        holding = await _held_games(redis)
    except Exception as error:  # a broken queue is a status line, not a crash
        report.bad("could not read the queue", str(error)[:80])
        return

    pending = sum(int(group.get("pending", 0)) for group in groups)
    detail = f"{pending} unacked · {_count(length)} entries retained"
    if pending > PENDING_WARN:
        report.warn("queue backing up", detail)
    else:
        report.ok("queue", detail)

    rows, marks, dead, orphaned = [], [], 0, 0
    for consumer in sorted(consumers, key=lambda c: int(c.get("idle", 0))):
        name = str(consumer.get("name", "?"))
        held = int(consumer.get("pending", 0))
        idle = int(int(consumer.get("idle", 0)) / 1000)

        alive = idle <= CONSUMER_DEAD_AFTER.total_seconds()
        if not alive and held == 0:
            dead += 1
            continue

        # What it is *doing*, which is the question a reader actually has. `XINFO` only counts
        # deliveries; the game id comes from the pending entries themselves.
        games = ", ".join(holding.get(name, []))
        if alive:
            doing = games or "waiting for work"
        else:
            # Gone, but still named on a delivery nobody has acked. The queue takes it back at
            # `RECLAIM_AFTER` and the turn simply reruns — it was rolled back whole (ADR-0007).
            left = RECLAIM_AFTER.total_seconds() - idle
            doing = f"{games or held} — orphaned, reclaimed in {_span(int(max(left, 0)))}"
            orphaned += held

        rows.append([_cut(name, 24, report.wide), doing, _span(idle)])
        marks.append(f"{GREEN}●{OFF}" if alive else f"{AMBER}▲{OFF}")

    if not rows:
        report.bad("no worker is consuming the queue", "nothing will play")
    else:
        report.table(["worker", "playing", "last seen"], rows, marks)
    if orphaned:
        report.warn(
            f"{orphaned} delivery(ies) held by workers that are gone",
            "a deploy or a crash mid-turn; the queue reclaims them and the turn reruns",
        )
    if dead:
        # Not a fault: a name outlives its process and only `XGROUP DELCONSUMER` clears it.
        report.plain(f"{dead} name(s) left behind by earlier worker processes")


async def _held_games(redis: Any) -> dict[str, list[str]]:
    """Which game each consumer is currently holding a job for.

    `XINFO CONSUMERS` gives a count and no identity, so the pending entries are read and their
    message bodies looked up — the job carries `game_id`, which is the only thing a reader wants
    to know when a worker has been busy for twenty minutes.
    """
    held: dict[str, list[str]] = {}
    entries = await redis.xpending_range(DEFAULT_STREAM, DEFAULT_GROUP, min="-", max="+", count=50)
    for entry in entries:
        consumer = str(entry.get("consumer", ""))
        message_id = str(entry.get("message_id", ""))
        rows = await redis.xrange(DEFAULT_STREAM, min=message_id, max=message_id)
        for _id, fields in rows:
            game = str(fields.get("game_id", ""))[:8]
            ply = str(fields.get("expected_ply", "?"))
            if game:
                held.setdefault(consumer, []).append(f"{game}@{ply}")
    return held


async def show_cooldowns(report: Report, redis: Any) -> None:
    """Endpoints and pools the matchmaker is currently avoiding (OPS-13)."""
    report.head("cooldowns")
    try:
        keys = await redis.keys(f"{COOLDOWN_PREFIX}:until:*")
        pool_keys = await redis.keys(f"{COOLDOWN_PREFIX}:provider:*")
    except Exception as error:  # a broken datastore is a status line, not a crash
        report.bad("could not read cooldowns", str(error)[:80])
        return

    rows = []
    for key in sorted(str(k) for k in keys):
        slug = key[len(f"{COOLDOWN_PREFIX}:until:") :]
        model, _, provider = slug.partition("|")
        ttl = await redis.ttl(key)
        rows.append(
            [_cut(model, 44, report.wide), _cut(provider, 20, report.wide), _span(max(ttl, 0))]
        )

    pools = sorted(str(k)[len(f"{COOLDOWN_PREFIX}:provider:") :] for k in pool_keys)
    if pools:
        # A whole pool resting affects every model that provider serves, which is why it is called
        # out rather than left to be inferred from a list of models (ADR-0017).
        report.warn(f"{len(pools)} shared pool(s) resting", ", ".join(pools))
    if rows:
        report.plain(f"{len(rows)} endpoint(s) resting")
        report.table(["model", "provider", "left"], rows)
    elif not pools:
        report.ok("nothing resting", "every endpoint is available")


async def show_games(report: Report, session: Any) -> None:
    report.head("games in flight")

    latest = (
        sa.select(GameEvent.game_id, sa.func.max(GameEvent.created_at).label("last_at"))
        .group_by(GameEvent.game_id)
        .subquery()
    )
    in_flight = list(
        (
            await session.execute(
                sa.select(Game, latest.c.last_at)
                .outerjoin(latest, latest.c.game_id == Game.id)
                .where(Game.status.in_([GameStatus.RUNNING, GameStatus.PAUSED]))
                .order_by(Game.status, latest.c.last_at.desc().nulls_last())
            )
        ).all()
    )
    if not in_flight:
        report.ok("nothing in flight", "no running or paused game")
        return

    rows: list[list[str]] = []
    marks: list[str] = []
    notes: list[str] = []
    # Whether each event is already running as many games as it may. A paused game that is due to
    # resume and cannot is waiting on this, not on a provider.
    at_capacity = await _events_at_capacity(session)

    for game, last_at in in_flight:
        blocked = await _event_of(session, game.id) in at_capacity
        seats = await _seat_names(session, game.id)
        waited, pauses = await _pause_history(session, game.id)
        stale = last_at is None or _now() - _aware(last_at) > STUCK_AFTER

        if game.status is GameStatus.PAUSED:
            # **"paused due" said nothing.** A game whose `resume_after` has passed and which is
            # still paused is not stuck: `reconciler.with_room_to_run` holds it because its event
            # is at its concurrency bound — a pool of one runs one game and queues the rest, which
            # is the whole point of the bound. Saying which of the two it is turns a puzzling line
            # into an obvious one.
            due = game.resume_after is None or _aware(game.resume_after) <= _now()
            state = (
                "waiting for a slot" if due and blocked else f"resumes {ahead(game.resume_after)}"
            )
            worry = waited / 86_400 > PAUSE_WARN_FRACTION
        else:
            state, worry = "live", stale

        rows.append(
            [
                str(game.id)[:8],
                str(game.ply_count),
                _cut(seats, 52, report.wide),
                state,
                ago(last_at),
                str(pauses) if pauses else "",
            ]
        )
        marks.append(f"{AMBER}▲{OFF}" if worry else f"{GREEN}●{OFF}")
        if worry:
            notes.append(
                f"{str(game.id)[:8]} — {game.pause_reason or 'no event since ' + ago(last_at)}"
            )
            if game.ply_count == 0 and pauses:
                notes.append(f"{str(game.id)[:8]} — has never played a ply")
            report.concerns.append(f"{AMBER}▲{OFF} game {str(game.id)[:8]} is not progressing")

    report.table(["game", "ply", "seats", "state", "last event", "pauses"], rows, marks)
    for note in notes:
        report.plain(note)


async def show_tournaments(report: Report, session: Any) -> None:
    report.head("tournaments")
    events = list(await session.scalars(sa.select(Tournament).order_by(Tournament.created_at)))
    if not events:
        report.plain("none")
        return

    rows, marks = [], []
    for event in events:
        pairings = list(
            await session.scalars(
                sa.select(TournamentGame).where(TournamentGame.tournament_id == event.id)
            )
        )
        total = len(pairings)
        scored = sum(1 for p in pairings if p.white_score is not None)
        abandoned = sum(1 for p in pairings if p.abandoned_reason and p.white_score is None)
        left = total - scored - abandoned
        share = abandoned / total if total else 0.0

        rows.append(
            [
                _cut(event.slug, 24, report.wide),
                event.status.value,
                f"{scored}/{total}",
                str(abandoned),
                str(left),
                str(event.max_concurrent),
            ]
        )
        # **A free pool abandons games, and that is not news.** Free endpoints go dark for hours
        # and a 24-hour patience window runs out; a third of the field lost is the ordinary shape
        # of a free event, and an alert that always fires is one nobody reads. The count stays in
        # the table either way — what changes is whether it interrupts.
        #
        # A *paid* event is different: an abandonment there is money spent for no result.
        free = await _uses_free_models(session, event)
        if share > (0.75 if free else 0.10):
            marks.append(f"{RED}✖{OFF}")
            report.concerns.append(f"{RED}✖{OFF} {event.slug}: {share:.0%} of the field abandoned")
        elif share > 0.25 and not free:
            marks.append(f"{AMBER}▲{OFF}")
            report.concerns.append(f"{AMBER}▲{OFF} {event.slug}: {share:.0%} abandoned")
        else:
            marks.append(f"{GREEN}●{OFF}")

    report.table(["event", "status", "settled", "aband", "to come", "conc"], rows, marks)


async def _uses_free_models(session: Any, event: Any) -> bool:
    """Whether any entrant is a free variant, and so is expected to be refused sometimes."""
    keys = list(
        await session.scalars(
            sa.select(TournamentGame.white_key).where(TournamentGame.tournament_id == event.id)
        )
    )
    return any(str(key).endswith(":free") for key in keys)


async def _events_at_capacity(session: Any) -> set[Any]:
    """Events already running `max_concurrent` games, so nothing else may start or resume."""
    at_capacity = set()
    for event in await session.scalars(sa.select(Tournament)):
        running = await session.scalar(
            sa.select(sa.func.count())
            .select_from(TournamentGame)
            .join(Game, Game.id == TournamentGame.game_id)
            .where(
                TournamentGame.tournament_id == event.id,
                TournamentGame.white_score.is_(None),
                TournamentGame.abandoned_reason.is_(None),
                Game.status.in_([GameStatus.PENDING, GameStatus.RUNNING]),
            )
        )
        if int(running or 0) >= event.max_concurrent:
            at_capacity.add(event.id)
    return at_capacity


async def _event_of(session: Any, game_id: Any) -> Any:
    return await session.scalar(
        sa.select(TournamentGame.tournament_id).where(TournamentGame.game_id == game_id)
    )


async def _seat_names(session: Any, game_id: Any) -> str:
    names = list(
        await session.scalars(
            sa.select(Player.display_name).where(Player.game_id == game_id).order_by(Player.colour)
        )
    )
    return " vs ".join(names) if names else "no seats"


async def _pause_history(session: Any, game_id: Any) -> tuple[float, int]:
    row = (
        await session.execute(
            sa.select(sa.func.count(GameEvent.id), sa.func.min(GameEvent.created_at)).where(
                GameEvent.game_id == game_id, GameEvent.type == EventType.GAME_PAUSED
            )
        )
    ).one()
    count, first = int(row[0] or 0), row[1]
    return (0.0 if first is None else (_now() - _aware(first)).total_seconds()), count


# ---------------------------------------------------------------------- entry point


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", action="store_true", help="only games in flight")
    parser.add_argument("--tournaments", action="store_true", help="only the events")
    parser.add_argument("--workers", action="store_true", help="only the queue and its consumers")
    parser.add_argument("--wide", action="store_true", help="do not truncate names")
    args = parser.parse_args()
    everything = not (args.games or args.tournaments or args.workers)

    report = Report(wide=args.wide)
    settings = get_settings()
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    sessionmaker = get_sessionmaker()

    try:
        if everything or args.workers:
            try:
                if everything:
                    await show_halt(report, redis)
                    await show_budgets(report, redis)
                await show_workers(report, redis)
                if everything:
                    await show_cooldowns(report, redis)
            except Exception as error:  # one dead datastore is not the whole report
                report.bad("could not read Redis", str(error)[:100])

        try:
            async with sessionmaker() as session:
                if everything:
                    await show_platform(report, session)
                if everything or args.games:
                    await show_games(report, session)
                if everything or args.tournaments:
                    await show_tournaments(report, session)
        except Exception as error:  # the same, for Postgres
            report.bad("could not read the database", str(error)[:100])

        print()
        if report.concerns:
            print(f"{BOLD}{len(report.concerns)} thing(s) worth a look{OFF}")
            for line in report.concerns:
                print(f"  {line}")
        else:
            print(f"{GREEN}{BOLD}all clear{OFF}")
        print()
        return 0
    finally:
        await redis.aclose()
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

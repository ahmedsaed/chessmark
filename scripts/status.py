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

from chessmark.core.budget import FreeTierBudget, GlobalBudget  # noqa: E402
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

#: A consumer holding a delivery longer than this is on a very slow turn or is dead. The queue
#: reclaims at fifteen minutes, so past that it is already being taken over.
CONSUMER_IDLE_WARN = dt.timedelta(minutes=15)

#: Past this with nothing held, a consumer is a ghost: Redis remembers a consumer name forever, so
#: every worker process that has ever run leaves one behind. Twenty-two of them at 2.6 days idle
#: told a reader nothing except that the container had been restarted a lot, and buried the one
#: line that mattered.
CONSUMER_GHOST_AFTER = dt.timedelta(hours=2)


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
    report.bad(f"HALTED ({state.source})", state.reason)
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

    free = FreeTierBudget(redis)
    used, usable = await free.used_today(), free.usable
    left = max(usable - used, 0)
    detail = f"{_count(used)} attempts of {_count(usable)} usable today"
    if left == 0:
        report.bad("free-model allowance spent", detail)
        # Worth being exact about whose number this is. It is *ours*, counted before each attempt
        # and deliberately an over-count — retries and calls that never reached a provider are in
        # it. A halt would mean OpenRouter refused us; this only means we decided to stop.
        report.plain("our own count of attempts, not a refusal from OpenRouter")
        report.plain("free games wait for the UTC midnight reset")
    elif left < usable * 0.15:
        report.warn("free-model allowance nearly spent", f"{detail} ({left} left)")
    else:
        report.ok("free tier", f"{detail} ({left} left)")


async def show_workers(report: Report, redis: Any) -> None:
    """Who is consuming the queue, and what each of them is holding."""
    report.head("workers")
    try:
        groups = await redis.xinfo_groups(DEFAULT_STREAM)
        consumers = await redis.xinfo_consumers(DEFAULT_STREAM, DEFAULT_GROUP)
        length = await redis.xlen(DEFAULT_STREAM)
    except Exception as error:  # a broken queue is a status line, not a crash
        report.bad("could not read the queue", str(error)[:80])
        return

    pending = sum(int(group.get("pending", 0)) for group in groups)
    detail = f"{pending} unacked · {_count(length)} entries retained"
    if pending > PENDING_WARN:
        report.warn("queue backing up", detail)
    else:
        report.ok("queue", detail)

    if not consumers:
        report.bad("no workers are consuming the queue", "nothing will play")
        return

    rows, marks, ghosts = [], [], 0
    for consumer in sorted(consumers, key=lambda c: int(c.get("idle", 0))):
        name = str(consumer.get("name", "?"))
        held = int(consumer.get("pending", 0))
        idle = int(int(consumer.get("idle", 0)) / 1000)

        if held == 0 and idle > CONSUMER_GHOST_AFTER.total_seconds():
            ghosts += 1
            continue

        stalled = held > 0 and idle > CONSUMER_IDLE_WARN.total_seconds()
        rows.append([_cut(name, 24, report.wide), str(held), _span(idle)])
        marks.append(f"{AMBER}▲{OFF}" if stalled else f"{GREEN}●{OFF}")
        if stalled:
            report.concerns.append(f"{AMBER}▲{OFF} worker {name} has held a job for {_span(idle)}")

    if not rows:
        report.warn("no worker has touched the queue recently", f"{ghosts} idle consumer(s)")
    else:
        report.table(["worker", "holding", "idle"], rows, marks)
    if ghosts:
        report.plain(f"{ghosts} consumer(s) from earlier worker processes, holding nothing")


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
    for game, last_at in in_flight:
        seats = await _seat_names(session, game.id)
        waited, pauses = await _pause_history(session, game.id)
        stale = last_at is None or _now() - _aware(last_at) > STUCK_AFTER

        if game.status is GameStatus.PAUSED:
            state, worry = (
                f"paused {ahead(game.resume_after)}",
                waited / 86_400 > PAUSE_WARN_FRACTION,
            )
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
        if share > 0.25:
            marks.append(f"{RED}✖{OFF}")
            report.concerns.append(f"{RED}✖{OFF} {event.slug}: {share:.0%} of the field abandoned")
        elif share > 0.1:
            marks.append(f"{AMBER}▲{OFF}")
            report.concerns.append(f"{AMBER}▲{OFF} {event.slug}: {share:.0%} abandoned")
        else:
            marks.append(f"{GREEN}●{OFF}")

    report.table(["event", "status", "settled", "aband", "to come", "conc"], rows, marks)

    for event in events:
        if event.status.value == "running":
            await _standings(report, session, event)


async def _standings(report: Report, session: Any, event: Any, top: int = 5) -> None:
    """The leading entrants, so a glance says who is actually winning."""
    pairings = list(
        await session.scalars(
            sa.select(TournamentGame).where(
                TournamentGame.tournament_id == event.id, TournamentGame.white_score.is_not(None)
            )
        )
    )
    if not pairings:
        return

    scores: dict[str, float] = {}
    for pairing in pairings:
        white = float(pairing.white_score)
        scores[pairing.white_key] = scores.get(pairing.white_key, 0.0) + white
        scores[pairing.black_key] = scores.get(pairing.black_key, 0.0) + (1.0 - white)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top]
    report.plain(f"{event.slug} — leaders")
    report.table(
        ["entrant", "points"],
        [[_cut(key, 48, report.wide), f"{points:g}"] for key, points in ranked],
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

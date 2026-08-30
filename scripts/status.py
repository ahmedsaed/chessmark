#!/usr/bin/env python3
"""Everything that is running, and everything that is stuck (OPS-21).

    ./chessmark status          # the whole picture
    ./chessmark status --games  # just one section

Written against a question that had no answer without four commands and a browser tab: *is the
harness healthy right now?* `docker ps` says the containers are up, which is not the same thing —
a stack can be entirely "up" while every game is paused behind a rate limit, the free allowance is
spent, and a pool has not moved a piece since yesterday.

**Colour is a judgement, not decoration.** Green means working, amber means worth a look, red means
somebody needs to do something. Anything printed in amber or red is repeated in a summary at the
bottom, so the answer to "is anything wrong" never depends on reading every line.

Read-only throughout, and every section is independently fault-tolerant: a section that cannot
reach its datastore prints what it could not read and the rest still renders. A status command that
dies because one thing is broken is a status command that is useless exactly when it is needed.
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
from chessmark.core.halt import Halt  # noqa: E402
from chessmark.db.enums import EventType, GameStatus  # noqa: E402
from chessmark.db.models import Game, GameEvent, Player, Tournament, TournamentGame  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.orchestration.queue import TurnQueue  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, AMBER = "\033[31m", "\033[32m", "\033[33m"

#: A running game whose last event is older than this has stopped moving without saying so. Above
#: the reconciler's own stale threshold, so a game it is about to rescue is not reported as stuck.
STUCK_AFTER = dt.timedelta(hours=1)

#: A paused game that has spent this much of its 24-hour patience is worth flagging: it is closer
#: to being abandoned than to being resumed, and that is a fact about a provider worth acting on.
PAUSE_WARN_FRACTION = 0.5

#: Queue depth above which something is not draining.
QUEUE_WARN = 50


class Report:
    """Lines to print, and the ones that were not green."""

    def __init__(self) -> None:
        self.concerns: list[str] = []

    def head(self, title: str) -> None:
        print(f"\n{AMBER}{title}{OFF}")

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  {GREEN}●{OFF} {label}{(' ' + DIM + detail + OFF) if detail else ''}")

    def warn(self, label: str, detail: str = "") -> None:
        print(f"  {AMBER}▲{OFF} {label}{(' ' + DIM + detail + OFF) if detail else ''}")
        self.concerns.append(f"{AMBER}▲{OFF} {label}")

    def bad(self, label: str, detail: str = "") -> None:
        print(f"  {RED}✖{OFF} {label}{(' ' + DIM + detail + OFF) if detail else ''}")
        self.concerns.append(f"{RED}✖{OFF} {label}")

    def plain(self, line: str) -> None:
        print(f"    {DIM}{line}{OFF}")


def ago(at: dt.datetime | None) -> str:
    if at is None:
        return "never"
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.UTC)
    seconds = int((dt.datetime.now(dt.UTC) - at).total_seconds())
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172_800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def ahead(at: dt.datetime | None) -> str:
    if at is None:
        return "unknown"
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.UTC)
    seconds = int((at - dt.datetime.now(dt.UTC)).total_seconds())
    if seconds <= 0:
        return "due now"
    if seconds < 5400:
        return f"in {max(seconds // 60, 1)}m"
    return f"in {seconds / 3600:.1f}h"


# ---------------------------------------------------------------------- sections


async def show_halt(report: Report, redis: Any) -> None:
    report.head("harness")
    state = await Halt(redis).state()
    if state is None:
        report.ok("running", "no halt")
        return

    # Red rather than amber: nothing is playing, and that is always worth acting on even when the
    # halt is deliberate.
    report.bad(f"HALTED ({state.source})", state.reason)
    report.plain(f"set {ago(state.at)}")
    if state.until is not None:
        report.plain(f"lifts by itself {ahead(state.until)}")
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
    detail = f"{used} of {usable} usable requests today ({left} left)"
    if left == 0:
        report.bad("free-model allowance spent", detail)
    elif left < usable * 0.15:
        report.warn("free-model allowance nearly spent", detail)
    else:
        report.ok("free tier", detail)


async def show_queue(report: Report, redis: Any) -> None:
    report.head("queue")
    queue = TurnQueue(redis)
    try:
        depth, pending = await queue.depth(), await queue.pending_count()
    except Exception as error:  # a broken queue is a status line, not a crash
        report.bad("could not read the queue", str(error)[:80])
        return

    detail = f"{depth} in the stream, {pending} delivered and unacked"
    if depth > QUEUE_WARN:
        report.warn("queue is deep", detail)
    else:
        report.ok("queue", detail)


async def show_games(report: Report, session: Any) -> None:
    report.head("games")

    counts = dict(
        (await session.execute(sa.select(Game.status, sa.func.count()).group_by(Game.status))).all()
    )
    total = sum(counts.values())
    summary = ", ".join(f"{n} {s.value}" for s, n in sorted(counts.items(), key=lambda kv: kv[0]))
    report.ok(f"{total} games", summary or "none")

    latest = (
        sa.select(GameEvent.game_id, sa.func.max(GameEvent.created_at).label("last_at"))
        .group_by(GameEvent.game_id)
        .subquery()
    )

    running = list(
        (
            await session.execute(
                sa.select(Game, latest.c.last_at)
                .outerjoin(latest, latest.c.game_id == Game.id)
                .where(Game.status == GameStatus.RUNNING)
                .order_by(latest.c.last_at.desc().nulls_last())
            )
        ).all()
    )
    for game, last_at in running:
        names = await _seat_names(session, game.id)
        label = f"live · ply {game.ply_count} · {names}"
        stale = last_at is None or (dt.datetime.now(dt.UTC) - _aware(last_at) > STUCK_AFTER)
        # A live game that has not written an event in an hour is not thinking; the reconciler's
        # own sweep is far shorter than that, so this means it has been requeued and is not
        # progressing.
        (report.warn if stale else report.ok)(label, f"last event {ago(_aware(last_at))}")
        if stale:
            report.plain(f"{game.id}")

    paused = list(
        await session.scalars(
            sa.select(Game)
            .where(Game.status == GameStatus.PAUSED)
            .order_by(Game.resume_after.asc().nulls_first())
        )
    )
    for game in paused:
        names = await _seat_names(session, game.id)
        waited, pauses = await _pause_history(session, game.id)
        label = f"paused · ply {game.ply_count} · {names}"
        detail = f"{pauses} pauses, {waited / 3600:.1f}h waited, resumes {ahead(game.resume_after)}"
        spent = waited / 86_400
        if spent > PAUSE_WARN_FRACTION:
            report.warn(label, detail)
            report.plain(f"{game.pause_reason or 'no reason recorded'}")
            if game.ply_count == 0:
                report.plain("has never played a ply — it will be abandoned when the window ends")
        else:
            report.ok(label, detail)


async def show_tournaments(report: Report, session: Any) -> None:
    report.head("tournaments")
    events = list(await session.scalars(sa.select(Tournament).order_by(Tournament.created_at)))
    if not events:
        report.plain("none")
        return

    for event in events:
        rows = list(
            await session.scalars(
                sa.select(TournamentGame).where(TournamentGame.tournament_id == event.id)
            )
        )
        pairings = len(rows)
        scored = sum(1 for r in rows if r.white_score is not None)
        abandoned = sum(1 for r in rows if r.abandoned_reason and r.white_score is None)
        # Whatever is neither scored nor abandoned is still to come — in flight or never started.
        # Derived rather than counted separately, because a pairing with no game *and* an
        # abandoned reason belongs in one bucket only, and counting both made the parts sum to
        # more than the whole.
        left = pairings - scored - abandoned

        label = f"{event.slug} · {event.status.value}"
        detail = f"{scored}/{pairings} settled, {abandoned} abandoned, {left} to come"

        # Abandonment is the number that says whether the harness is healthy, and a third of a
        # field lost is the shape the first pool had before ADR-0021 and ADR-0022.
        share = abandoned / pairings if pairings else 0
        if share > 0.25:
            report.bad(label, detail)
            report.plain(f"{share:.0%} of the field abandoned — read ./chessmark logs worker")
        elif share > 0.1:
            report.warn(label, detail)
        else:
            report.ok(label, detail)

        if left and event.status.value == "running":
            report.plain(f"{event.max_concurrent} at a time")


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
    if first is None:
        return 0.0, count
    return (dt.datetime.now(dt.UTC) - _aware(first)).total_seconds(), count


def _aware(at: dt.datetime) -> dt.datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=dt.UTC)


# ---------------------------------------------------------------------- entry point


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", action="store_true", help="only the games section")
    parser.add_argument("--tournaments", action="store_true", help="only the tournaments section")
    args = parser.parse_args()
    everything = not (args.games or args.tournaments)

    report = Report()
    settings = get_settings()
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    sessionmaker = get_sessionmaker()

    try:
        if everything:
            try:
                await show_halt(report, redis)
                await show_budgets(report, redis)
                await show_queue(report, redis)
            except Exception as error:  # one dead datastore is not the whole report
                report.bad("could not read Redis", str(error)[:100])

        try:
            async with sessionmaker() as session:
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

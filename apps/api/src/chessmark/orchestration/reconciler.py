"""Rescue games that stopped moving.

The queue's own `XAUTOCLAIM` covers the common crash: a worker died holding a job, another takes
it over. This covers the case it cannot — a job that no longer exists at all, because Redis was
restarted or flushed, or because a bug dropped it.

Postgres is the authority on what should be running (ADR-0008), so the reconciler asks it rather
than the queue: any game marked `running` whose last event is older than a threshold is stalled,
and gets a fresh `advance_turn` for wherever it actually is. Enqueuing one for a game that turns
out to be fine is harmless — `expected_ply` makes the duplicate a no-op.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, GameEvent
from chessmark.orchestration.queue import AdvanceTurn, TurnQueue

log = logging.getLogger(__name__)

DEFAULT_STALE_AFTER = dt.timedelta(minutes=20)


@dataclass(slots=True)
class ReconcileReport:
    requeued: list[str] = field(default_factory=list)
    checked: int = 0

    def __str__(self) -> str:
        return f"checked {self.checked} running games, requeued {len(self.requeued)}"


async def find_stalled(
    session: AsyncSession, *, stale_after: dt.timedelta = DEFAULT_STALE_AFTER
) -> list[Game]:
    """Running games whose most recent event is older than `stale_after`.

    Games that have never emitted an event count as stalled too — one created and marked running
    but never enqueued would otherwise sit forever.
    """
    cutoff = dt.datetime.now(dt.UTC) - stale_after
    latest_event = (
        sa.select(
            GameEvent.game_id.label("game_id"),
            sa.func.max(GameEvent.created_at).label("last_at"),
        )
        .group_by(GameEvent.game_id)
        .subquery()
    )

    query = (
        sa.select(Game)
        .outerjoin(latest_event, latest_event.c.game_id == Game.id)
        .where(
            Game.status == GameStatus.RUNNING,
            sa.or_(
                latest_event.c.last_at.is_(None),
                latest_event.c.last_at < cutoff,
            ),
        )
        .order_by(Game.created_at)
    )

    return list(await session.scalars(query))


async def reconcile(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: TurnQueue,
    *,
    stale_after: dt.timedelta = DEFAULT_STALE_AFTER,
) -> ReconcileReport:
    report = ReconcileReport()

    async with sessionmaker() as session:
        stalled = await find_stalled(session, stale_after=stale_after)
        report.checked = len(stalled)
        jobs = [AdvanceTurn(game_id=game.id, expected_ply=game.ply_count) for game in stalled]

    for job in jobs:
        await queue.enqueue(job)
        report.requeued.append(str(job.game_id))
        log.info("requeued stalled game %s at ply %s", job.game_id, job.expected_ply)

    return report

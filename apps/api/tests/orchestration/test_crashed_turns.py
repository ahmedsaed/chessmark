"""A turn that crashes is recorded for the operator, and the worker survives it (OPS-21).

`c4550202` crashed on every resume for a day — a unique-constraint violation the worker had no rule
for. The exception escaped `run_forever` and ended the process, the job had already been acked, and
the game sat silent until the stall sweep requeued it forty-five minutes later. The only trace was
a traceback that `./chessmark logs` had stopped showing long before anybody looked.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import plays
from chessmark.core.failures import KEEP, FailureLog
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, GameEvent
from chessmark.orchestration.queue import AdvanceTurn
from chessmark.orchestration.worker import CRASHED, HandledJob
from tests.orchestration.conftest import Fixture, run_next


def crashing(worker: Any) -> Any:
    async def handle(_job: AdvanceTurn) -> HandledJob:
        raise ValueError("duplicate key value violates unique constraint\n[parameters: ...]")

    worker.handle = handle
    return worker


async def test_a_crash_does_not_end_the_worker(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    worker = crashing(make_worker(plays(["e4"]), publish=True))

    handled = await run_next(worker, game.queue)

    assert handled is not None
    assert handled.outcome == CRASHED
    assert await game.queue.pending_count() == 0, "the delivery is acked, not left to reclaim"


async def test_a_crash_is_recorded_for_status(
    db: AsyncSession, game: Fixture, make_worker: Any, redis: Any
) -> None:
    worker = crashing(make_worker(plays(["e4"]), publish=True))

    await run_next(worker, game.queue)

    [failure] = await FailureLog(redis).recent(dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    assert failure.game_id == str(game.game.id)
    assert failure.ply == 0
    assert failure.error == "ValueError"
    assert failure.message.startswith("duplicate key value")


async def test_a_crash_is_not_shown_to_spectators(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """The game's log is what the page reads. A stack trace is the operator's, not the reader's."""
    before = await db.scalar(
        sa.select(sa.func.count()).select_from(GameEvent).where(GameEvent.game_id == game.game.id)
    )
    worker = crashing(make_worker(plays(["e4"]), publish=True))

    await run_next(worker, game.queue)

    db.expunge_all()
    after = await db.scalar(
        sa.select(sa.func.count()).select_from(GameEvent).where(GameEvent.game_id == game.game.id)
    )
    assert after == before
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.status is GameStatus.RUNNING, "left for the stall sweep to rescue"


async def test_the_log_keeps_the_newest_and_stops_at_the_window(redis: Any) -> None:
    import uuid

    log = FailureLog(redis)
    for ply in range(KEEP + 5):
        await log.record(uuid.uuid4(), ply, RuntimeError(f"crash {ply}"))

    everything = await log.recent(dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    assert len(everything) == KEEP
    assert everything[0].ply == KEEP + 4, "newest first"

    assert await log.recent(dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1)) == []


async def test_a_database_error_is_recorded_by_its_cause(redis: Any) -> None:
    """The wrapper filled the column and the constraint's name was cut off after it."""
    import uuid

    error = Exception(
        "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class "
        "'asyncpg.exceptions.UniqueViolationError'>: duplicate key value violates unique "
        'constraint "uq_tool_calls_turn_id_sequence"'
    )
    log = FailureLog(redis)
    await log.record(uuid.uuid4(), 54, error)

    [failure] = await log.recent(dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    assert failure.message.startswith("duplicate key value violates unique constraint")

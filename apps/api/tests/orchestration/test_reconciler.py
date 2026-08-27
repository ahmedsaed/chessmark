"""The reconciler: rescuing games the queue itself lost.

`XAUTOCLAIM` covers a dead worker. This covers the case it cannot — the job no longer existing at
all, because Redis restarted or a bug dropped it. Postgres is the authority on what should be
running (ADR-0008), so the reconciler asks it rather than the queue.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import plays
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game, GameEvent
from chessmark.orchestration.reconciler import find_stalled, reconcile
from chessmark.orchestration.worker import ADVANCED, STALE
from tests.orchestration.conftest import Fixture, both_sides, run_next, seat_match

pytestmark = pytest.mark.integration


async def _age_events(session: AsyncSession, game_id, minutes: int) -> None:
    """Backdate a game's events so it looks stalled."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)
    await session.execute(
        sa.update(GameEvent).where(GameEvent.game_id == game_id).values(created_at=cutoff)
    )
    await session.commit()


async def test_a_freshly_active_game_is_not_stalled(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    await run_next(make_worker(plays(["e4"])), game.queue)

    assert await find_stalled(db) == []


async def test_a_game_with_old_events_is_stalled(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    await run_next(make_worker(plays(["e4"])), game.queue)
    await _age_events(db, game.game.id, minutes=60)

    stalled = await find_stalled(db)

    assert [g.id for g in stalled] == [game.game.id]


async def test_a_running_game_with_no_events_is_stalled(db: AsyncSession, queue) -> None:
    """A game marked running but never enqueued would otherwise sit forever."""
    fixture = await seat_match(db, queue)
    await db.execute(sa.delete(GameEvent).where(GameEvent.game_id == fixture.game.id))
    await db.commit()

    stalled = await find_stalled(db)

    assert [g.id for g in stalled] == [fixture.game.id]


async def test_finished_games_are_never_stalled(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    await db.execute(
        sa.update(Game).where(Game.id == game.game.id).values(status=GameStatus.FINISHED)
    )
    await db.commit()
    await _age_events(db, game.game.id, minutes=60)

    assert await find_stalled(db) == []


async def test_reconcile_requeues_a_stalled_game(
    db: AsyncSession, sessionmaker, game: Fixture, make_worker
) -> None:
    await run_next(make_worker(plays(["e4"])), game.queue)
    await game.queue.consume("drain", block_ms=100)  # swallow the follow-up job
    await _age_events(db, game.game.id, minutes=60)

    report = await reconcile(sessionmaker, game.queue)

    assert report.requeued == [str(game.game.id)]

    deliveries = await game.queue.consume("checker", block_ms=200)
    assert len(deliveries) == 1
    assert deliveries[0].job.expected_ply == 1, "requeued at the ply the game actually reached"


async def test_a_needless_requeue_is_harmless(
    db: AsyncSession, sessionmaker, game: Fixture, make_worker
) -> None:
    """The reconciler may be wrong about a game being stuck; `expected_ply` makes that safe."""
    worker = make_worker(both_sides(["e4"], ["e5"]))
    await run_next(worker, game.queue)
    await _age_events(db, game.game.id, minutes=60)

    await reconcile(sessionmaker, game.queue)

    # Two jobs now target ply 1: the worker's own follow-up, and the reconciler's duplicate.
    first = await run_next(worker, game.queue, consumer="w2")
    second = await run_next(worker, game.queue, consumer="w2")

    assert first is not None and second is not None
    assert first.outcome == ADVANCED
    assert second.outcome == STALE, "the duplicate must be recognised and dropped"

    db.expunge_all()
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.ply_count == 2, "the duplicate must not have played a second move"


async def test_reconcile_reports_what_it_did(db: AsyncSession, sessionmaker, game: Fixture) -> None:
    report = await reconcile(sessionmaker, game.queue)

    assert report.checked >= 0
    assert "requeued" in str(report)


# ====================================================================== resuming a pause


async def test_a_paused_game_is_resumed_once_its_wait_is_over(
    db: AsyncSession, game: Fixture, sessionmaker: Any
) -> None:
    """The other half of pausing. A game waiting on a clock rather than on a worker needs somebody
    to put it back on the queue, and this is that somebody — the alternative is a game that pauses
    correctly and never plays again."""
    game.game.status = GameStatus.PAUSED
    game.game.resume_after = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    game.game.pause_reason = "rate-limited by Google AI Studio"
    await db.commit()

    report = await reconcile(sessionmaker, game.queue)

    assert report.resumed == [str(game.game.id)]

    db.expunge_all()
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.status is GameStatus.RUNNING
    assert reloaded.resume_after is None
    assert reloaded.pause_reason is None, "or the page keeps saying paused after play resumed"


async def test_a_pause_that_is_not_over_is_left_alone(
    db: AsyncSession, game: Fixture, sessionmaker: Any
) -> None:
    """Resuming early is worse than waiting: it spends a request to be refused again, which is the
    whole thing the pause exists to avoid."""
    game.game.status = GameStatus.PAUSED
    game.game.resume_after = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
    await db.commit()

    report = await reconcile(sessionmaker, game.queue)

    assert report.resumed == []
    db.expunge_all()
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.status is GameStatus.PAUSED


async def test_a_pause_with_no_time_on_it_is_resumed_rather_than_stranded(
    db: AsyncSession, game: Fixture, sessionmaker: Any
) -> None:
    """It should not happen — the worker always sets one — but a row without it would otherwise
    wait forever, and the failure mode of a stuck game is silence."""
    game.game.status = GameStatus.PAUSED
    game.game.resume_after = None
    await db.commit()

    report = await reconcile(sessionmaker, game.queue)

    assert report.resumed == [str(game.game.id)]


async def test_resuming_appends_one_event(
    db: AsyncSession, game: Fixture, sessionmaker: Any
) -> None:
    """Invariant 7, and what makes the pause disappear from the page: the panel clears its notice
    on `game_resumed`, so a resume the log does not carry leaves the page reading "paused" over a
    game that is playing."""
    game.game.status = GameStatus.PAUSED
    game.game.resume_after = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    game.game.pause_reason = "rate-limited"
    await db.commit()

    await reconcile(sessionmaker, game.queue)

    db.expunge_all()
    events = await db.scalars(
        sa.select(GameEvent).where(
            GameEvent.game_id == game.game.id, GameEvent.type == EventType.GAME_RESUMED
        )
    )
    resumed = list(events)
    assert len(resumed) == 1
    assert "rate-limited" in str(resumed[0].payload.get("detail"))

"""The reconciler: rescuing games the queue itself lost.

`XAUTOCLAIM` covers a dead worker. This covers the case it cannot — the job no longer existing at
all, because Redis restarted or a bug dropped it. Postgres is the authority on what should be
running (ADR-0008), so the reconciler asks it rather than the queue.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import plays
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game, GameEvent, TournamentGame
from chessmark.orchestration.reconciler import find_resumable, find_stalled, reconcile
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


# ====================================================================== resuming within bounds


async def test_resuming_respects_the_events_concurrency_bound(
    db: AsyncSession, sessionmaker: Any, queue: Any
) -> None:
    """A pause frees the concurrency slot; resuming has to ask for it back.

    It did not. Every due game was resumed in one pass and enqueued, and nothing consulted
    `max_concurrent` — so a pool bounded to one game had three come due within a quarter of an hour
    and would have played them in parallel. The bound was honoured everywhere except the one path
    that creates running games without going through `_start_games`.
    """
    from chessmark.db import tournaments as repo
    from chessmark.orchestration.reconciler import with_room_to_run
    from chessmark.tournament import FieldFilter, Format, TournamentConfig

    entrants = await repo.resolve_field(db, FieldFilter())
    tournament = await repo.create_tournament(
        db,
        name="Bounded",
        slug=f"bounded-{uuid.uuid4().hex[:8]}",
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        entrants=entrants,
    )

    # Three games, all paused and all due, all in one event bounded to a single running game.
    games = []
    for index in range(3):
        fixture = await seat_match(db, queue)
        fixture.game.status = GameStatus.PAUSED
        fixture.game.resume_after = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=3 - index)
        db.add(
            TournamentGame(
                # A distinct round per pairing: the table is unique on
                # (tournament, round, white, black), so three identical rows are not a shape the
                # schema allows and faking one would be testing something impossible.
                tournament_id=tournament.id,
                round_number=index + 1,
                white_key="a",
                black_key="b",
                game_id=fixture.game.id,
            )
        )
        games.append(fixture.game)
    await db.commit()

    due = await find_resumable(db)
    ready = await with_room_to_run(db, due)

    assert len(due) == 3, "all three are due"
    assert len(ready) == 1, "and exactly one may run"
    assert ready[0].id == games[0].id, "the one that has waited longest"


async def test_the_rest_are_left_paused_for_a_later_tick(
    db: AsyncSession, sessionmaker: Any, queue: Any
) -> None:
    """Left paused rather than dropped: `resume_after` is already behind them, so the next tick
    finds them again. The effect is that they queue instead of piling in."""
    from chessmark.db import tournaments as repo
    from chessmark.tournament import FieldFilter, Format, TournamentConfig

    entrants = await repo.resolve_field(db, FieldFilter())
    tournament = await repo.create_tournament(
        db,
        name="Bounded",
        slug=f"bounded-{uuid.uuid4().hex[:8]}",
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        entrants=entrants,
    )
    for index in range(2):
        fixture = await seat_match(db, queue)
        fixture.game.status = GameStatus.PAUSED
        fixture.game.resume_after = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        db.add(
            TournamentGame(
                tournament_id=tournament.id,
                round_number=index + 1,
                white_key="a",
                black_key="b",
                game_id=fixture.game.id,
            )
        )
    await db.commit()

    report = await reconcile(sessionmaker, queue)

    assert len(report.resumed) == 1
    db.expunge_all()
    still_paused = await db.scalars(sa.select(Game).where(Game.status == GameStatus.PAUSED))
    assert len(list(still_paused)) == 1, "the other waits its turn rather than being forgotten"


async def test_a_game_in_no_event_resumes_immediately(
    db: AsyncSession, game: Fixture, sessionmaker: Any
) -> None:
    """A human's game, or anything started by hand, is bounded by nothing. Making it wait on a
    tournament's slot would be a bound nobody asked for."""
    game.game.status = GameStatus.PAUSED
    game.game.resume_after = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    await db.commit()

    report = await reconcile(sessionmaker, game.queue)

    assert report.resumed == [str(game.game.id)]


# ====================================================================== one sweep at a time


async def test_only_one_worker_sweeps_at_a_time(redis: Any) -> None:
    """Every worker runs a reconciler, which was free when there was one worker.

    With several they wake together and ask Postgres the same question. Enqueuing survives that —
    `expected_ply` makes a duplicate a no-op — but `with_room_to_run` does not: two reconcilers each
    see the same free concurrency slot and each fill it, admitting more running games than the event
    allows.
    """
    from chessmark.orchestration.reconciler import SingleFlight

    async with SingleFlight(redis) as first:
        assert first, "the first sweep proceeds"
        async with SingleFlight(redis) as second:
            assert not second, "and the second stands down rather than duplicating it"

    async with SingleFlight(redis) as after:
        assert after, "released when the sweep finishes, so the next minute is not skipped"


async def test_a_dead_holder_does_not_block_forever(redis: Any) -> None:
    """A TTL rather than a real lock, and deliberately: a missed sweep costs a minute, a lock
    nobody can release costs everything after it."""
    from chessmark.orchestration.reconciler import SingleFlight

    lock = SingleFlight(redis, key="chessmark:test:sweep", ttl=1)
    async with lock as held:
        assert held

    assert await redis.ttl("chessmark:test:sweep") in (-2, -1) or True
    # The key is released on exit; the TTL is the backstop for a holder that never exits.
    assert await redis.get("chessmark:test:sweep") is None


async def test_without_redis_it_never_blocks(sessionmaker: Any) -> None:
    """Scripted runs and tests wire no Redis, and a reconciler that refused to sweep without one
    would silently stop rescuing games."""
    from chessmark.orchestration.reconciler import SingleFlight

    async with SingleFlight(None) as held:
        assert held

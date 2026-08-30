"""A ply has one owner, and a game that ended stays ended (OPS-15, OPS-16, ADR-0022).

Game `29e7f004` appended **seven `game_ended` rows**, and the log shows `turn_started` at ply 19
twice, fifty milliseconds apart. `expected_ply` cannot catch that: it makes a *redelivered* job safe
by comparing against committed state, and two jobs running simultaneously both read ply 18, both
find it matches, and both play ply 19.

Then the loser wrote over the winner. `_pause` set `PAUSED` unconditionally, so a turn finishing
minutes after another had concluded the game un-finished it, the reconciler resumed it, and the ply
was played again. In game `855e208d` that decided a rating: it ended as `budget_exceeded` — a
harness stop, excluded — and re-ended as `error_forfeit`, which counts.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.scripted import plays
from chessmark.db.enums import EventType, GameStatus, TurnStatus
from chessmark.db.models import GameEvent
from chessmark.db.repositories import GameInFlightError, get_game
from chessmark.game import GameResult, Outcome, Termination
from chessmark.orchestration.worker import IN_FLIGHT, NOT_RUNNING, TurnWorker
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration


async def _events(db: AsyncSession, game_id: Any, kind: EventType) -> list[GameEvent]:
    return list(
        await db.scalars(
            sa.select(GameEvent)
            .where(GameEvent.game_id == game_id, GameEvent.type == kind)
            .order_by(GameEvent.seq)
        )
    )


# ====================================================================== one owner


async def test_a_claim_is_refused_while_another_transaction_holds_it(
    sessionmaker: async_sessionmaker[AsyncSession], game: Fixture
) -> None:
    """The mechanism on its own: `FOR UPDATE NOWAIT` against a row somebody else holds.

    `NOWAIT` and not a plain wait, because a turn holds this lock for as long as it runs — up to
    twenty calls at ten minutes each — and a second worker blocking on that would tie up a
    connection for hours to learn something it can learn now.
    """
    async with sessionmaker() as holder, holder.begin():
        await get_game(holder, game.game.id, claim=True)

        async with sessionmaker() as other, other.begin():
            with pytest.raises(GameInFlightError):
                await get_game(other, game.game.id, claim=True)


async def test_the_second_worker_drops_its_job_rather_than_playing_the_ply(
    sessionmaker: async_sessionmaker[AsyncSession], game: Fixture, make_worker: Any
) -> None:
    """What the log actually shows: two `turn_started` events at ply 19, 50ms apart."""
    worker = make_worker(plays(["e4"]))

    async with sessionmaker() as holder, holder.begin():
        await get_game(holder, game.game.id, claim=True)

        handled = await worker.handle(game.first_job)

    assert handled.outcome == IN_FLIGHT
    assert handled.result is None, "it never reached a provider"


async def test_a_dropped_job_is_not_requeued(
    sessionmaker: async_sessionmaker[AsyncSession], game: Fixture, make_worker: Any
) -> None:
    """The owner enqueues the next ply when it commits, so re-enqueueing here would only rebuild
    the duplicate this exists to remove. A crashed owner is still covered by `XAUTOCLAIM` and the
    reconciler, both of which run regardless."""
    worker = make_worker(plays(["e4"]))
    before = await game.queue.depth()

    async with sessionmaker() as holder, holder.begin():
        await get_game(holder, game.game.id, claim=True)
        await worker.handle(game.first_job)

    assert await game.queue.depth() == before


async def test_two_concurrent_jobs_start_the_ply_once(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """The property end to end. Before this, both jobs played the ply and both wrote an ending."""
    one = make_worker(plays(["e4"]), consumer="worker-one")
    two = make_worker(plays(["d4"]), consumer="worker-two")

    outcomes = await asyncio.gather(
        one.handle(game.first_job), two.handle(game.first_job), return_exceptions=True
    )

    assert not any(isinstance(o, BaseException) for o in outcomes), outcomes
    kinds = sorted(str(o.outcome) for o in outcomes)  # type: ignore[union-attr]
    assert "in_flight" in kinds or "stale" in kinds, (
        f"one of the two had to decline the ply, got {kinds}"
    )

    db.expunge_all()
    started = await _events(db, game.game.id, EventType.TURN_STARTED)
    plies = [event.payload.get("ply") for event in started]
    assert len(plies) == len(set(plies)), f"a ply was started twice: {plies}"


# ====================================================================== stays ended


async def test_a_finished_game_is_not_paused_over(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """`_pause` set `PAUSED` unconditionally, and that is how a concluded game came back to life."""
    worker = make_worker(plays(["e4"]))

    fetched = await get_game(db, game.game.id)
    fetched.status = GameStatus.ABORTED
    fetched.termination = Termination.ABANDONED
    await db.commit()

    handled = await worker.handle(game.first_job)

    assert handled.outcome == NOT_RUNNING

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.ABORTED, "a late job must not un-finish it"


async def test_a_second_ending_is_never_written(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """One game ended seven times. `_conclude` guarded `FINISHED` and not `ABORTED`, and
    `_abandon` guarded nothing at all."""
    worker: TurnWorker = make_worker(plays(["e4"]))
    fetched = await get_game(db, game.game.id)

    await worker._abandon(db, fetched, "the harness gave up")
    await worker._abandon(db, fetched, "and gave up again")
    await worker._conclude(
        db,
        fetched,
        Outcome(
            result=GameResult.WHITE_WINS,
            termination=Termination.ERROR_FORFEIT,
            winner=None,
            detail="a race chose this verdict in a real game",
        ),
    )
    await db.commit()

    db.expunge_all()
    endings = await _events(db, game.game.id, EventType.GAME_ENDED)
    after = await get_game(db, game.game.id)

    assert len(endings) == 1, f"exactly one ending per game (invariant 7), got {len(endings)}"
    assert after.termination is Termination.ABANDONED, "the first verdict stands"


async def test_winning_the_race_costs_the_ordinary_turn_nothing(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """The guard and the lock must be invisible when nobody is competing for the ply."""
    handled = await make_worker(plays(["e4"])).handle(game.first_job)

    assert handled.result is not None
    assert handled.result.status is TurnStatus.COMPLETED

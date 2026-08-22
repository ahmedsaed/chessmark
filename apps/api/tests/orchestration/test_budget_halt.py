"""The global kill switch, at the point where money is actually spent (AUTH-05).

The Phase 9 exit criterion is specific: *no LLM call is issued* when the budget is tripped, and the
test must fail if the provider is called. So the provider here is a spy that raises on contact —
asserting on a spend counter afterwards would pass just as happily if the call had been made and
merely not billed.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.budget import GlobalBudget
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, LlmCall, Turn
from chessmark.orchestration.worker import BUDGET, GLOBAL_BUDGET, TurnWorker
from tests.orchestration.conftest import Fixture, run_next

pytestmark = pytest.mark.integration


class ProviderWasCalledError(AssertionError):
    """Raised by the spy. If this escapes, the kill switch did not hold."""


class Spy:
    """A provider that refuses to be called, and remembers being asked.

    Both halves matter. Raising stops the call from doing anything; the flag is what makes the
    failure *legible* — the gateway classifies a raised exception as a provider failure and turns
    it into a failed turn, so without the flag a leaked call would surface as `turn_failed` and
    read like an unrelated bug.
    """

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.called = True
        raise ProviderWasCalledError("the provider was called while the global budget was tripped")


@pytest.fixture
def budget(redis: Any) -> GlobalBudget:
    return GlobalBudget(redis, daily_limit_usd=Decimal("1.00"))


def _worker(sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget, fn: Any) -> TurnWorker:
    from chessmark.agents.llm import LlmGateway

    return TurnWorker(
        sessionmaker=sessionmaker,
        queue=queue,
        gateway=LlmGateway(completion_fn=fn),
        redis=None,
        consumer="budget-test",
        budget=budget,
    )


# ====================================================================== the criterion


async def test_no_llm_call_is_issued_once_the_budget_is_tripped(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    """The exit criterion, asserted the only way that means anything: the provider raises if it is
    reached at all."""
    await budget.record(Decimal("1.00"))
    assert await budget.tripped()

    spy = Spy()
    worker = _worker(sessionmaker, queue, redis, budget, spy)
    handled = await run_next(worker, game.queue)

    assert not spy.called, "the kill switch leaked: a provider call was issued"
    assert handled is not None
    assert handled.outcome == GLOBAL_BUDGET


async def test_nothing_is_written_when_the_switch_halts_a_turn(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    """A halt must leave no trace: no turn row, no LLM call, no ply."""
    await budget.record(Decimal("1.00"))
    spy = Spy()
    worker = _worker(sessionmaker, queue, redis, budget, spy)

    await run_next(worker, game.queue)

    assert not spy.called
    db.expunge_all()
    assert (await db.scalars(sa.select(Turn))).all() == []
    assert (await db.scalars(sa.select(LlmCall))).all() == []


async def test_a_halted_game_is_not_forfeited(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    """Neither model did anything wrong. Forfeiting one for *our* budget running out would put an
    operational decision into the benchmark results — the same reasoning as a provider outage
    abandoning rather than forfeiting."""
    await budget.record(Decimal("1.00"))
    spy = Spy()
    worker = _worker(sessionmaker, queue, redis, budget, spy)

    await run_next(worker, game.queue)

    assert not spy.called
    db.expunge_all()
    stored = await db.get(Game, game.game.id)
    assert stored is not None
    assert stored.status is GameStatus.RUNNING
    assert stored.result.value == "*"


async def test_a_game_runs_normally_below_the_limit(
    db: AsyncSession,
    game: Fixture,
    sessionmaker: Any,
    queue: Any,
    redis: Any,
    budget: GlobalBudget,
    make_worker: Any,
) -> None:
    """The switch must not be permanently on — the obvious way to pass every test above."""
    from tests.support import both_sides

    worker = _worker(sessionmaker, queue, redis, budget, both_sides(["e4"], ["e5"]))
    handled = await run_next(worker, game.queue)

    assert handled is not None
    assert handled.outcome != GLOBAL_BUDGET
    assert handled.outcome != BUDGET


# ====================================================================== accounting


async def test_a_turns_cost_reaches_the_global_counter(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    """Without this the counter never rises and the switch never trips, which would make every
    test above vacuously true in production."""
    from tests.support import both_sides

    worker = _worker(sessionmaker, queue, redis, budget, both_sides(["e4"], ["e5"], cost=0.002))
    await run_next(worker, game.queue)

    db.expunge_all()
    turn = (await db.scalars(sa.select(Turn))).first()
    assert turn is not None
    assert turn.cost_usd > 0, "the scripted turn must cost something or this asserts nothing"
    assert await budget.spent_today() == turn.cost_usd


async def test_spend_is_attributed_to_the_user_who_started_the_game(
    db: AsyncSession, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    """Layer 2 only works if the per-user ledger actually fills up (AUTH-03)."""
    import uuid

    from chessmark.db.models import User
    from chessmark.db.quotas import usage_for
    from chessmark.orchestration.match import Seat, create_match, start_match
    from tests.support import both_sides

    user = User(clerk_user_id=f"user_{uuid.uuid4().hex[:8]}")
    db.add(user)
    await db.flush()

    match = await create_match(
        db,
        white=Seat(display_name="white-model", model="scripted/white"),
        black=Seat(display_name="black-model", model="scripted/black"),
        created_by_user_id=user.id,
    )
    job = await start_match(db, queue, game_id=match.game.id)
    await db.commit()
    await queue.enqueue(job)

    worker = _worker(sessionmaker, queue, redis, budget, both_sides(["e4"], ["e5"], cost=0.002))
    await run_next(worker, queue, consumer="budget-test")

    async with sessionmaker() as session:
        spent = (await usage_for(session, user.id)).usd_spent
    assert spent > 0


async def test_an_unowned_game_still_records_global_spend(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    """CLI and scripted games have no owner. The global counter is the layer that does not care
    who is spending, so it must still see the money."""
    from tests.support import both_sides

    worker = _worker(sessionmaker, queue, redis, budget, both_sides(["e4"], ["e5"], cost=0.002))
    await run_next(worker, game.queue)

    assert await budget.spent_today() > 0


async def test_yesterdays_spend_does_not_hold_today_hostage(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, redis: Any, budget: GlobalBudget
) -> None:
    yesterday = dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=1)
    await budget.record(Decimal("100"), day=yesterday)

    assert not await budget.tripped()

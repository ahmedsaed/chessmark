"""Credits — a granted balance, spent to start a game (ADR-0016).

The interesting test is the concurrent one. Everything else here is bookkeeping; the race is the
part that decides whether a balance is a limit or a suggestion.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import credit_cost_for
from chessmark.db.credits import (
    InsufficientCreditsError,
    balance_of,
    charge,
    cost_of,
    grant,
    refund,
)
from chessmark.db.models import ModelRegistry, User

pytestmark = pytest.mark.integration


async def _user(db: AsyncSession, *, credits: int = 0) -> uuid.UUID:
    user = User(clerk_user_id=f"user_{uuid.uuid4().hex[:8]}", credit_balance=credits)
    db.add(user)
    await db.flush()
    return user.id


async def _model(db: AsyncSession, slug: str, *, cost: int, override: int | None = None) -> None:
    db.add(
        ModelRegistry(
            openrouter_id=slug,
            display_name=slug,
            provider=slug.split("/")[0],
            credit_cost=cost,
            credit_cost_override=override,
        )
    )
    await db.flush()


# ====================================================================== the tiers


def test_a_model_is_priced_by_whichever_price_is_worse() -> None:
    """Both prices must fit a tier for a model to qualify for it.

    A model cheap to prompt and ruinous to generate can still empty a budget, and the failure is
    asymmetric: pricing one too low costs money, pricing one too high costs a user one credit.
    """
    cheap_in_dear_out = credit_cost_for(Decimal("0.0000001"), Decimal("0.00005"))

    assert cheap_in_dear_out > 1


@pytest.mark.parametrize(
    ("prompt_per_m", "completion_per_m", "expected"),
    [
        ("0.00", "0.00", 1),  # free models still occupy a seat
        ("0.30", "1.50", 1),  # exactly on the tier-1 ceiling
        ("0.31", "1.50", 2),  # a cent over, and it is tier 2
        ("2.00", "8.00", 2),
        ("10.00", "40.00", 3),
        ("30.00", "180.00", 6),  # gpt-5.5-pro
    ],
)
def test_tier_boundaries(prompt_per_m: str, completion_per_m: str, expected: int) -> None:
    cost = credit_cost_for(Decimal(prompt_per_m) / 1_000_000, Decimal(completion_per_m) / 1_000_000)

    assert cost == expected


# ====================================================================== what a game costs


async def test_a_game_costs_the_sum_of_its_seats(db: AsyncSession) -> None:
    await _model(db, "vendor/cheap", cost=1)
    await _model(db, "vendor/dear", cost=6)

    assert await cost_of(db, ["vendor/cheap", "vendor/dear"]) == 7


async def test_the_same_model_twice_is_charged_twice(db: AsyncSession) -> None:
    """A model playing itself is still two seats, and costs two provider bills."""
    await _model(db, "vendor/twin", cost=2)

    assert await cost_of(db, ["vendor/twin", "vendor/twin"]) == 4


async def test_an_administrator_override_wins_over_the_derived_price(db: AsyncSession) -> None:
    """Re-seeding rewrites the derived column, so an exception has to live somewhere else."""
    await _model(db, "vendor/exception", cost=6, override=1)

    assert await cost_of(db, ["vendor/exception"]) == 1


async def test_an_unknown_model_costs_the_top_tier_not_nothing(db: AsyncSession) -> None:
    """An unknown price is not a free one."""
    assert await cost_of(db, ["vendor/never-registered"]) == 6


async def test_a_game_with_no_machine_seat_is_free(db: AsyncSession) -> None:
    assert await cost_of(db, []) == 0


# ====================================================================== charging


async def test_charging_leaves_the_remainder(db: AsyncSession) -> None:
    user = await _user(db, credits=10)

    assert await charge(db, user, 3) == 7
    assert await balance_of(db, user) == 7


async def test_a_balance_can_be_spent_exactly(db: AsyncSession) -> None:
    user = await _user(db, credits=2)

    assert await charge(db, user, 2) == 0


async def test_charging_more_than_the_balance_is_refused(db: AsyncSession) -> None:
    user = await _user(db, credits=1)

    with pytest.raises(InsufficientCreditsError) as caught:
        await charge(db, user, 2)

    assert caught.value.needed == 2
    assert caught.value.held == 1
    # The message has to say both numbers: "insufficient credits" alone leaves the reader unable
    # to tell whether they are one short or twenty.
    assert "2 credits" in str(caught.value)
    assert "you have 1" in str(caught.value)


async def test_a_refused_charge_leaves_the_balance_untouched(db: AsyncSession) -> None:
    user = await _user(db, credits=1)

    with pytest.raises(InsufficientCreditsError):
        await charge(db, user, 5)

    assert await balance_of(db, user) == 1


async def test_a_new_account_holds_nothing(db: AsyncSession) -> None:
    """Zero by default is the whole point: nobody plays until an administrator says so."""
    user = await _user(db)

    assert await balance_of(db, user) == 0
    with pytest.raises(InsufficientCreditsError):
        await charge(db, user, 1)


# ====================================================================== the race


async def test_concurrent_charges_cannot_overspend(db: AsyncSession, sessionmaker: Any) -> None:
    """The attack, not a hypothetical.

    Anyone trying to spend a credit twice fires requests in parallel, so a `SELECT` then `UPDATE`
    lets ten requests all read "3 credits" and all proceed. Postgres has to decide it, which is
    why the check lives in the `WHERE` clause of the update.

    Each charge gets its own session and commits, because the race only exists across
    transactions — one session cannot race itself.
    """
    user = await _user(db, credits=3)
    await db.commit()

    async def attempt() -> bool:
        async with sessionmaker() as session:
            try:
                await charge(session, user, 1)
                await session.commit()
            except InsufficientCreditsError:
                return False
            return True

    granted = sum(await asyncio.gather(*(attempt() for _ in range(20))))

    assert granted == 3, f"balance let {granted} games through, not 3"
    async with sessionmaker() as session:
        assert await balance_of(session, user) == 0


# ====================================================================== granting


async def test_granting_adds_to_the_balance(db: AsyncSession) -> None:
    user = await _user(db, credits=2)

    assert await grant(db, user, 5) == 7


async def test_credits_can_be_taken_away(db: AsyncSession) -> None:
    user = await _user(db, credits=5)

    assert await grant(db, user, -3) == 2


async def test_taking_away_more_than_is_held_stops_at_zero(db: AsyncSession) -> None:
    """A negative balance would be a debt to work off before playing again, which is not what
    anyone means by removing credits."""
    user = await _user(db, credits=2)

    assert await grant(db, user, -10) == 0


async def test_a_refund_returns_what_a_game_cost(db: AsyncSession) -> None:
    user = await _user(db, credits=6)
    await charge(db, user, 4)

    assert await refund(db, user, 4) == 6


async def test_granting_to_a_stranger_raises(db: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await grant(db, uuid.uuid4(), 5)


async def test_a_balance_does_not_regenerate(db: AsyncSession) -> None:
    """The point of the change (ADR-0016). There is no day, so there is nothing to roll over."""
    user = await _user(db, credits=1)
    await charge(db, user, 1)

    assert await balance_of(db, user) == 0
    with pytest.raises(InsufficientCreditsError):
        await charge(db, user, 1)

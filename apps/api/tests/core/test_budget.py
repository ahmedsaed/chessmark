"""The global daily kill switch.

The layer that trusts nothing (ADR-0011). Its job is to be correct about money and to be correct
at midnight, so the tests are about exactness and about the day boundary.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from chessmark.core.budget import GlobalBudget, key_for, to_units, to_usd

pytestmark = pytest.mark.integration

DAY = dt.date(2026, 8, 22)


@pytest.fixture
def budget(redis: Any) -> GlobalBudget:
    return GlobalBudget(redis, daily_limit_usd=Decimal("1.00"))


# ====================================================================== exactness


def test_a_cost_survives_the_round_trip_exactly() -> None:
    """Invariant 4. A kill switch that quietly loses fractions of a cent per call is not an
    accounting of anything."""
    assert to_usd(to_units(Decimal("0.07620359"))) == Decimal("0.07620359")


def test_tiny_costs_round_up_rather_than_to_zero() -> None:
    """`gemini-2.5-flash-lite` bills fractions this small. Rounding to nearest would let a large
    number of sub-unit calls spend real money while registering as nothing at all."""
    assert to_units(Decimal("0.000000001")) == 1
    assert to_units(Decimal("0")) == 0
    assert to_units(Decimal("-5")) == 0


async def test_a_thousand_small_calls_accumulate_without_drift(budget: GlobalBudget) -> None:
    """The case floats would get wrong. `INCRBYFLOAT` accumulates error; integers do not."""
    for _ in range(1000):
        await budget.record(Decimal("0.00000001"), day=DAY)

    assert await budget.spent_today(day=DAY) == Decimal("0.00001000")


# ====================================================================== tripping


async def test_a_fresh_day_has_spent_nothing(budget: GlobalBudget) -> None:
    assert await budget.spent_today(day=DAY) == Decimal(0)
    assert not await budget.tripped(day=DAY)


async def test_the_switch_trips_at_the_limit(budget: GlobalBudget) -> None:
    await budget.record(Decimal("0.99999999"), day=DAY)
    assert not await budget.tripped(day=DAY)

    await budget.record(Decimal("0.00000001"), day=DAY)
    assert await budget.tripped(day=DAY)


async def test_the_switch_stays_tripped_once_over(budget: GlobalBudget) -> None:
    await budget.record(Decimal("5.00"), day=DAY)

    assert await budget.tripped(day=DAY)
    assert await budget.remaining_usd(day=DAY) == Decimal(0)


async def test_remaining_never_goes_negative(budget: GlobalBudget) -> None:
    await budget.record(Decimal("2.50"), day=DAY)

    assert await budget.remaining_usd(day=DAY) == Decimal(0)


async def test_an_unset_limit_means_no_limit_not_no_spending(redis: Any) -> None:
    """A default of zero must not read as "refuse everything". An operator who has not configured a
    budget has not asked us to halt; halting on their behalf is the more surprising failure."""
    unlimited = GlobalBudget(redis, daily_limit_usd=Decimal(0))
    await unlimited.record(Decimal("1000"), day=DAY)

    assert not await unlimited.tripped(day=DAY)
    assert await unlimited.remaining_usd(day=DAY) is None


# ====================================================================== the day boundary


async def test_the_counter_resets_at_utc_midnight(budget: GlobalBudget) -> None:
    """No sweeper, no cron: the key name contains the date, so the day rolls over by itself. A
    reset that depends on a job running is a reset that eventually does not happen."""
    await budget.record(Decimal("5.00"), day=DAY)

    tomorrow = DAY + dt.timedelta(days=1)
    assert await budget.tripped(day=DAY)
    assert not await budget.tripped(day=tomorrow)
    assert await budget.spent_today(day=tomorrow) == Decimal(0)


async def test_each_day_gets_its_own_key(budget: GlobalBudget) -> None:
    assert key_for(DAY) != key_for(DAY + dt.timedelta(days=1))
    assert DAY.isoformat() in key_for(DAY)


async def test_the_counter_expires_on_its_own(budget: GlobalBudget, redis: Any) -> None:
    """Otherwise every day leaves a key behind forever."""
    await budget.record(Decimal("1.00"), day=DAY)

    assert await redis.ttl(key_for(DAY)) > 0


async def test_the_ttl_is_refreshed_on_every_write(budget: GlobalBudget, redis: Any) -> None:
    """Setting the TTL only when the key is new has a race where two writers both see it existing
    and neither sets one, leaving a key that never expires."""
    await budget.record(Decimal("1.00"), day=DAY)
    await redis.persist(key_for(DAY))
    assert await redis.ttl(key_for(DAY)) == -1

    await budget.record(Decimal("1.00"), day=DAY)
    assert await redis.ttl(key_for(DAY)) > 0

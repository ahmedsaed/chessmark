"""Per-user daily counters — a record, not a limit.

The gating these used to do moved to `test_credits.py` when ADR-0016 replaced the daily quota with
a granted balance. What is left records what happened, which the admin spend view reads.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.models import User
from chessmark.db.quotas import (
    note_game_started,
    record_spend,
    reset_quota,
    usage_for,
)

pytestmark = pytest.mark.integration

DAY = dt.date(2026, 8, 22)


async def _user(db: AsyncSession, suffix: str = "a") -> uuid.UUID:
    user = User(clerk_user_id=f"user_{suffix}_{uuid.uuid4().hex[:8]}", email=f"{suffix}@test")
    db.add(user)
    await db.flush()
    return user.id


# ====================================================================== counting


async def test_a_new_user_has_used_nothing(db: AsyncSession) -> None:
    usage = await usage_for(db, await _user(db), day=DAY)

    assert usage.games_started == 0
    assert usage.usd_spent == Decimal(0)


async def test_starting_a_game_counts_it(db: AsyncSession) -> None:
    user = await _user(db)

    assert (await note_game_started(db, user, day=DAY)).games_started == 1
    assert (await note_game_started(db, user, day=DAY)).games_started == 2
    assert (await usage_for(db, user, day=DAY)).games_started == 2


async def test_spend_accumulates(db: AsyncSession) -> None:
    user = await _user(db)

    await record_spend(db, user, Decimal("0.07620359"), day=DAY)
    total = await record_spend(db, user, Decimal("0.00000001"), day=DAY)

    assert total == Decimal("0.07620360")


async def test_spend_can_be_recorded_before_any_game_is_reserved(db: AsyncSession) -> None:
    """A game started yesterday keeps spending today, on a day with no ledger row yet."""
    user = await _user(db)

    assert await record_spend(db, user, Decimal("1.00"), day=DAY) == Decimal("1.00")
    assert (await usage_for(db, user, day=DAY)).games_started == 0


async def test_the_ledger_is_per_day(db: AsyncSession) -> None:
    user = await _user(db)
    await note_game_started(db, user, day=DAY)

    tomorrow = DAY + dt.timedelta(days=1)
    assert (await note_game_started(db, user, day=tomorrow)).games_started == 1


async def test_the_ledger_is_per_user(db: AsyncSession) -> None:
    one, two = await _user(db, "one"), await _user(db, "two")
    await note_game_started(db, one, day=DAY)

    assert (await note_game_started(db, two, day=DAY)).games_started == 1


# ====================================================================== admin


async def test_an_admin_can_reset_a_quota(db: AsyncSession) -> None:
    """For when our own bug consumed someone's allowance (AUTH-08)."""
    user = await _user(db)
    await note_game_started(db, user, day=DAY)

    await reset_quota(db, user, day=DAY)

    assert (await usage_for(db, user, day=DAY)).games_started == 0
    assert (await note_game_started(db, user, day=DAY)).games_started == 1


async def test_resetting_one_day_leaves_another_alone(db: AsyncSession) -> None:
    user = await _user(db)
    await note_game_started(db, user, day=DAY)
    await note_game_started(db, user, day=DAY + dt.timedelta(days=1))

    await reset_quota(db, user, day=DAY)

    assert (await usage_for(db, user, day=DAY + dt.timedelta(days=1))).games_started == 1

"""Per-user daily quotas.

The interesting test is the concurrent one. Everything else here is bookkeeping; the race is the
part that decides whether the quota is a limit or a suggestion.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.models import User
from chessmark.db.quotas import (
    QuotaExceededError,
    record_spend,
    reserve_game,
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


async def test_reserving_a_game_counts_it(db: AsyncSession) -> None:
    user = await _user(db)

    assert (await reserve_game(db, user, max_games=3, day=DAY)).games_started == 1
    assert (await reserve_game(db, user, max_games=3, day=DAY)).games_started == 2
    assert (await usage_for(db, user, day=DAY)).games_started == 2


async def test_the_quota_refuses_the_game_over_the_line(db: AsyncSession) -> None:
    user = await _user(db)
    for _ in range(2):
        await reserve_game(db, user, max_games=2, day=DAY)

    with pytest.raises(QuotaExceededError) as caught:
        await reserve_game(db, user, max_games=2, day=DAY)

    assert caught.value.reason == "games"
    assert caught.value.used == 2
    assert caught.value.limit == 2


async def test_the_refusal_says_which_limit_and_how_much(db: AsyncSession) -> None:
    """ "Quota exceeded" with no numbers leaves the user with nothing to act on."""
    user = await _user(db)
    await reserve_game(db, user, max_games=1, day=DAY)

    with pytest.raises(QuotaExceededError, match="games quota reached"):
        await reserve_game(db, user, max_games=1, day=DAY)


# ====================================================================== the race


async def test_concurrent_reservations_cannot_exceed_the_quota(
    db: AsyncSession, sessionmaker: Any
) -> None:
    """The attack, not a hypothetical.

    Anyone trying to beat a quota fires requests in parallel, so a `SELECT` then `UPDATE` lets ten
    requests all read "0 of 3" and all proceed. Postgres has to decide it, which is why the check
    lives in the `ON CONFLICT ... WHERE` clause.

    Each reservation gets its own session and commits, because the race only exists across
    transactions — one session cannot race itself.
    """
    user = await _user(db)
    await db.commit()

    async def attempt() -> bool:
        async with sessionmaker() as session:
            try:
                await reserve_game(session, user, max_games=3, day=DAY)
                await session.commit()
            except QuotaExceededError:
                return False
            return True

    granted = sum(await asyncio.gather(*(attempt() for _ in range(20))))

    assert granted == 3, f"quota let {granted} games through, not 3"
    async with sessionmaker() as session:
        assert (await usage_for(session, user, day=DAY)).games_started == 3


# ====================================================================== spend


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


async def test_a_user_over_their_spend_limit_cannot_start_another_game(db: AsyncSession) -> None:
    """The most a daily spend cap can promise: a call's cost is unknown until it returns, so the
    limit is enforced at the *next* game rather than mid-call."""
    user = await _user(db)
    await record_spend(db, user, Decimal("5.00"), day=DAY)

    with pytest.raises(QuotaExceededError) as caught:
        await reserve_game(db, user, max_games=100, max_usd=Decimal("2.00"), day=DAY)

    assert caught.value.reason == "spend"


async def test_a_user_under_their_spend_limit_may_continue(db: AsyncSession) -> None:
    user = await _user(db)
    await record_spend(db, user, Decimal("1.00"), day=DAY)

    assert (
        await reserve_game(db, user, max_games=100, max_usd=Decimal("2.00"), day=DAY)
    ).games_started == 1


async def test_an_unset_spend_limit_does_not_block(db: AsyncSession) -> None:
    user = await _user(db)
    await record_spend(db, user, Decimal("999"), day=DAY)

    assert (await reserve_game(db, user, max_games=5, max_usd=None, day=DAY)).games_started == 1
    assert (
        await reserve_game(db, user, max_games=5, max_usd=Decimal(0), day=DAY)
    ).games_started == 2


# ====================================================================== the day boundary


async def test_quotas_are_per_day(db: AsyncSession) -> None:
    user = await _user(db)
    await reserve_game(db, user, max_games=1, day=DAY)

    tomorrow = DAY + dt.timedelta(days=1)
    assert (await reserve_game(db, user, max_games=1, day=tomorrow)).games_started == 1


async def test_quotas_are_per_user(db: AsyncSession) -> None:
    one, two = await _user(db, "one"), await _user(db, "two")
    await reserve_game(db, one, max_games=1, day=DAY)

    assert (await reserve_game(db, two, max_games=1, day=DAY)).games_started == 1


# ====================================================================== admin


async def test_an_admin_can_reset_a_quota(db: AsyncSession) -> None:
    """For when our own bug consumed someone's allowance (AUTH-08)."""
    user = await _user(db)
    await reserve_game(db, user, max_games=1, day=DAY)

    await reset_quota(db, user, day=DAY)

    assert (await usage_for(db, user, day=DAY)).games_started == 0
    assert (await reserve_game(db, user, max_games=1, day=DAY)).games_started == 1


async def test_resetting_one_day_leaves_another_alone(db: AsyncSession) -> None:
    user = await _user(db)
    await reserve_game(db, user, max_games=5, day=DAY)
    await reserve_game(db, user, max_games=5, day=DAY + dt.timedelta(days=1))

    await reset_quota(db, user, day=DAY)

    assert (await usage_for(db, user, day=DAY + dt.timedelta(days=1))).games_started == 1

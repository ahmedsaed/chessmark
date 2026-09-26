"""Credit — dollars, spent at what each turn actually cost (ADR-0052).

What is under test is bookkeeping, and the one property it all serves: the balance and its history
agree, including across the one thing this design allows that ADR-0016 did not — a balance that
ends a turn below zero.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import price_tier_for
from chessmark.db.credits import (
    InsufficientCreditError,
    balance_of,
    can_play,
    grant,
    history_of,
    ledger_total,
    require_credit,
    spend,
)
from chessmark.db.enums import CreditReason
from chessmark.db.models import User
from tests.support import seat_match

pytestmark = pytest.mark.integration


async def _user(db: AsyncSession, *, usd: str = "0") -> uuid.UUID:
    """A user with a balance, seeded **through the ledger**.

    Setting `balance_usd` directly would leave a balance with no history, which is the one thing
    the ledger is supposed to make impossible — and a helper that quietly does it would let the
    invariant tests pass on data production can never produce.
    """
    user = User(clerk_user_id=f"user_{uuid.uuid4().hex[:8]}")
    db.add(user)
    await db.flush()
    if Decimal(usd):
        await grant(db, user.id, Decimal(usd), note="test fixture")
    return user.id


async def _game(db: AsyncSession, queue: Any) -> uuid.UUID:
    fixture = await seat_match(db, queue)
    return fixture.match.game.id


# ====================================================================== the bands


def test_a_model_is_banded_by_whichever_price_is_worse() -> None:
    """Cheap to prompt and expensive to generate is an expensive model to play."""
    assert price_tier_for(Decimal("0.0000001"), Decimal("0.00005")) > 1


@pytest.mark.parametrize(
    ("prompt_per_m", "completion_per_m", "expected"),
    [
        ("0.00", "0.00", 1),
        ("0.30", "1.50", 1),  # exactly on the first ceiling
        ("0.31", "1.50", 2),  # a cent over, and it is the second band
        ("2.00", "8.00", 2),
        ("10.00", "40.00", 3),
        ("30.00", "180.00", 4),  # gpt-5.5-pro
    ],
)
def test_band_boundaries(prompt_per_m: str, completion_per_m: str, expected: int) -> None:
    tier = price_tier_for(Decimal(prompt_per_m) / 1_000_000, Decimal(completion_per_m) / 1_000_000)

    assert tier == expected


# ====================================================================== who may play


async def test_a_new_account_holds_nothing_and_cannot_play(db: AsyncSession) -> None:
    user = await _user(db)

    assert await balance_of(db, user) == 0
    assert not await can_play(db, user)
    with pytest.raises(InsufficientCreditError) as caught:
        await require_credit(db, user)
    # The refusal says what is held — "insufficient credit" alone is a dead end.
    assert "$0.00" in str(caught.value)


async def test_any_balance_above_zero_can_play(db: AsyncSession) -> None:
    """No estimate of what a game will cost: nothing knows that until it is played (ADR-0052)."""
    user = await _user(db, usd="0.0001")

    assert await can_play(db, user)
    await require_credit(db, user)


# ====================================================================== spending


async def test_a_turn_is_charged_what_it_cost(db: AsyncSession, queue: Any) -> None:
    user = await _user(db, usd="1")
    game = await _game(db, queue)

    entry = await spend(db, user, Decimal("0.0123"), game_id=game, turn_id=None)

    assert await balance_of(db, user) == Decimal("0.9877")
    assert entry is not None
    assert entry.reason is CreditReason.TURN
    assert entry.game_id == game
    assert entry.actor_user_id is None  # nobody decides a charge


async def test_a_turn_is_charged_even_past_zero(db: AsyncSession, queue: Any) -> None:
    """**The debit is never refused.** The money was spent by the time the cost is known; refusing
    to record it would only make the ledger lie about the provider bill."""
    user = await _user(db, usd="0.01")
    game = await _game(db, queue)

    await spend(db, user, Decimal("0.03"), game_id=game, turn_id=None)

    assert await balance_of(db, user) == Decimal("-0.02")
    assert not await can_play(db, user)


async def test_a_free_turn_writes_nothing(db: AsyncSession, queue: Any) -> None:
    user = await _user(db, usd="1")
    game = await _game(db, queue)
    before = len(await history_of(db, user))

    assert await spend(db, user, Decimal(0), game_id=game, turn_id=None) is None
    assert len(await history_of(db, user)) == before


async def test_concurrent_turns_lose_no_debit(
    db: AsyncSession, sessionmaker: Any, queue: Any
) -> None:
    """Two games of one person finishing turns together. A read-then-write would let one debit
    overwrite the other; the charge is one statement, so every cent lands."""
    user = await _user(db, usd="1")
    game = await _game(db, queue)
    await db.commit()

    async def turn() -> None:
        async with sessionmaker() as session:
            await spend(session, user, Decimal("0.01"), game_id=game, turn_id=None)
            await session.commit()

    await asyncio.gather(*(turn() for _ in range(20)))

    async with sessionmaker() as session:
        assert await balance_of(session, user) == Decimal("0.80")
        assert await ledger_total(session, user) == Decimal("0.80")


# ====================================================================== granting


async def test_granting_adds_to_the_balance(db: AsyncSession) -> None:
    user = await _user(db, usd="2")

    assert await grant(db, user, Decimal("5.50")) == Decimal("7.50")


async def test_credit_can_be_taken_away(db: AsyncSession) -> None:
    user = await _user(db, usd="5")

    assert await grant(db, user, Decimal(-3)) == Decimal(2)


async def test_taking_away_more_than_is_held_stops_at_zero(db: AsyncSession) -> None:
    """A negative balance would be a debt, which is not what anyone means by removing credit."""
    user = await _user(db, usd="2")

    assert await grant(db, user, Decimal(-10)) == 0


async def test_taking_away_never_raises_a_balance_below_zero(db: AsyncSession, queue: Any) -> None:
    """The floor is the lower of zero and where the balance was. A plain clamp at zero would turn
    a revocation from an overrun balance into a *grant* of the overrun."""
    user = await _user(db, usd="0.01")
    await spend(db, user, Decimal("0.03"), game_id=await _game(db, queue), turn_id=None)
    before = len(await history_of(db, user))

    assert await grant(db, user, Decimal(-5)) == Decimal("-0.02")
    assert len(await history_of(db, user)) == before  # nothing moved, so nothing is written


async def test_a_grant_to_an_overrun_balance_counts_from_where_it_is(
    db: AsyncSession, queue: Any
) -> None:
    user = await _user(db, usd="0.01")
    await spend(db, user, Decimal("0.03"), game_id=await _game(db, queue), turn_id=None)

    assert await grant(db, user, Decimal(5)) == Decimal("4.98")


async def test_granting_to_a_stranger_raises(db: AsyncSession) -> None:
    with pytest.raises(LookupError):
        await grant(db, uuid.uuid4(), Decimal(5))


# ====================================================================== the ledger (AUTH-13)


async def test_a_balance_equals_the_sum_of_its_history(db: AsyncSession, queue: Any) -> None:
    """The property the whole ledger exists for, through every kind of movement — including a turn
    that takes the balance below zero and a revocation the floor stops."""
    user = await _user(db)
    game = await _game(db, queue)

    await grant(db, user, Decimal(1))
    await spend(db, user, Decimal("0.4"), game_id=game, turn_id=None)
    await grant(db, user, Decimal("-0.5"))
    await spend(db, user, Decimal("0.3"), game_id=game, turn_id=None)
    await grant(db, user, Decimal(-1))
    await grant(db, user, Decimal(2))

    assert await balance_of(db, user) == Decimal("1.8")
    assert await ledger_total(db, user) == Decimal("1.8")


async def test_every_movement_is_recorded_with_its_reason(db: AsyncSession, queue: Any) -> None:
    user = await _user(db)

    await grant(db, user, Decimal(10), note="beta invite")
    await spend(db, user, Decimal(2), game_id=await _game(db, queue), turn_id=None)
    await grant(db, user, Decimal(-1))

    rows = await history_of(db, user)

    assert [row.reason for row in rows] == [
        CreditReason.ADMIN_REVOKE,
        CreditReason.TURN,
        CreditReason.ADMIN_GRANT,
    ]
    assert {row.unit for row in rows} == {"usd"}
    assert rows[-1].note == "beta invite"


async def test_a_grant_records_who_made_it(db: AsyncSession) -> None:
    """A balance that cannot name the person who moved it explains nothing."""
    admin = await _user(db)
    user = await _user(db)

    await grant(db, user, Decimal(5), actor_user_id=admin, note="why not")

    entry = (await history_of(db, user))[0]
    assert entry.actor_user_id == admin
    assert entry.note == "why not"


async def test_a_clamped_revocation_records_what_actually_happened(db: AsyncSession) -> None:
    """Revoking $10 from a balance of $2 moves it by $2. A ledger that stored the *requested*
    amount would stop summing to the balance."""
    user = await _user(db, usd="2")

    await grant(db, user, Decimal(-10))

    entry = (await history_of(db, user))[0]
    assert entry.delta == -2
    assert entry.balance_after == 0
    assert await ledger_total(db, user) == await balance_of(db, user)


async def test_credit_rows_do_not_count_toward_a_dollar_balance(db: AsyncSession) -> None:
    """The rows from before ADR-0052 were credits, closed by a `retired` row. Summing them into
    dollars would put a tester's old grant of 5 credits on their balance as $5."""
    from chessmark.db.models import CreditLedger

    user = await _user(db)
    db.add_all(
        [
            CreditLedger(
                user_id=user,
                delta=Decimal(5),
                balance_after=Decimal(5),
                reason=CreditReason.ADMIN_GRANT,
                unit="credit",
            ),
            CreditLedger(
                user_id=user,
                delta=Decimal(-5),
                balance_after=Decimal(0),
                reason=CreditReason.RETIRED,
                unit="credit",
            ),
            CreditLedger(
                user_id=user,
                delta=Decimal(7),
                balance_after=Decimal(7),
                reason=CreditReason.ADMIN_GRANT,
                unit="credit",
            ),
        ]
    )
    await db.flush()

    assert await ledger_total(db, user) == 0

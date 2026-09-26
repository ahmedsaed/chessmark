"""Credit — a balance in US dollars, spent at what each turn actually cost (ADR-0052, AUTH-10).

ADR-0016 made a credit a unit of play: a game cost one to six of them at the door, by the price
band of its models. That was access control, not accounting, and it could not become money — two
games in one band cost an order of magnitude apart. So a balance is now dollars, and a game draws
on it **turn by turn, at the cost the provider's own token counts give** (invariant 4). There is
no estimate, no hold, and nothing to refund: a turn that is rolled back was never charged.

**The debit is never refused.** The money for a turn is spent by the time its cost is known, so
`spend` records what happened rather than asking permission. What stops play is the check before a
turn — `can_play`, `balance > 0` — and a game whose payer is out pauses until credit is added. The
balance can therefore sit below zero by the one turn in flight per running game, and never more.

**The debit rides in the turn's own transaction**, beside the lines that add the same number to
`players.total_cost_usd` and `games.total_cost_usd`. They commit together or not at all, so what a
game says it cost and what its owner was charged cannot disagree.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import CreditReason
from chessmark.db.models import CreditLedger, User

ZERO = Decimal(0)


class InsufficientCreditError(Exception):
    """The caller holds nothing to play with.

    Carries the balance so the API can say it — "insufficient credit" alone does not tell somebody
    at $0.00 from somebody the one turn below it.
    """

    def __init__(self, *, held: Decimal) -> None:
        super().__init__(f"You have ${held:.2f} of credit. A game against a paid model needs more.")
        self.held = held


async def balance_of(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    balance = await session.scalar(sa.select(User.balance_usd).where(User.id == user_id))
    return Decimal(balance or 0)


async def can_play(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Whether this person's balance can pay for another turn: anything above zero.

    Not "enough for a game" — nothing knows what a game will cost until it has been played, and
    guessing is exactly what ADR-0052 replaced.
    """
    return await balance_of(session, user_id) > ZERO


async def require_credit(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Refuse to start a paid game for someone with nothing to pay with."""
    held = await balance_of(session, user_id)
    if held <= ZERO:
        raise InsufficientCreditError(held=held)


async def spend(
    session: AsyncSession,
    user_id: uuid.UUID,
    cost: Decimal,
    *,
    game_id: uuid.UUID,
    turn_id: int | None,
) -> CreditLedger | None:
    """Charge one turn's actual cost to the person who started the game. Never refused.

    One statement, so two games of the same person finishing turns together cannot lose either
    debit to the other's read. A turn that cost nothing — a `:free` model — writes nothing: a
    ledger of zero rows is a ledger nobody reads.
    """
    if cost <= ZERO:
        return None

    remaining = (
        await session.execute(
            sa.update(User)
            .where(User.id == user_id)
            .values(balance_usd=User.balance_usd - cost)
            .returning(User.balance_usd)
        )
    ).scalar_one_or_none()
    if remaining is None:
        # The account was deleted while its game played on. Nobody left to charge, and the turn
        # itself is real and must still be recorded — so this is not an error.
        return None

    entry = CreditLedger(
        user_id=user_id,
        delta=-cost,
        balance_after=Decimal(remaining),
        reason=CreditReason.TURN,
        game_id=game_id,
        turn_id=turn_id,
    )
    session.add(entry)
    return entry


async def grant(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal,
    *,
    actor_user_id: uuid.UUID | None = None,
    note: str | None = None,
    reason: CreditReason | None = None,
) -> Decimal:
    """Add credit to a balance, or take it away, and return the new balance (AUTH-11, AUTH-13).

    **Taking it away stops at zero, and never lifts a balance that is already below it.** A
    negative balance is the one turn a game overran by (see the module note); revoking from it
    would otherwise *raise* it to zero, which is a grant wearing a revocation's reason. So the floor
    is the lower of zero and where the balance already was.

    **The floor is why `balance_after` is recorded rather than derived.** Revoking $10 from a
    balance of $2 moves it by $2, not $10, so a ledger that stored only the requested amount would
    not sum to the balance. The row records what actually happened.
    """
    before = await balance_of(session, user_id)

    total = (
        await session.execute(
            sa.update(User)
            .where(User.id == user_id)
            .values(
                balance_usd=sa.func.greatest(
                    User.balance_usd + amount, sa.func.least(User.balance_usd, 0)
                )
            )
            .returning(User.balance_usd)
        )
    ).scalar_one_or_none()
    if total is None:
        raise LookupError(f"no user with id {user_id}")

    after = Decimal(total)
    if after != before:
        session.add(
            CreditLedger(
                user_id=user_id,
                delta=after - before,
                balance_after=after,
                reason=reason
                or (CreditReason.ADMIN_GRANT if amount > 0 else CreditReason.ADMIN_REVOKE),
                actor_user_id=actor_user_id,
                note=note,
            )
        )
    return after


async def history_of(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 100
) -> list[CreditLedger]:
    """A balance's history, newest first — both units, so the credits era stays visible."""
    rows = await session.scalars(
        sa.select(CreditLedger)
        .where(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.id.desc())
        .limit(limit)
    )
    return list(rows)


async def ledger_total(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """What the dollar history says the balance should be.

    `users.balance_usd` is the enforcement point and this is the account of it; they must agree,
    and a test replays every ledger to prove it. Only `usd` rows count: the `credit` rows before
    ADR-0052 are closed to zero by their own `retired` row and were never dollars.
    """
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(CreditLedger.delta), 0)).where(
            CreditLedger.user_id == user_id, CreditLedger.unit == "usd"
        )
    )
    return Decimal(total or 0)

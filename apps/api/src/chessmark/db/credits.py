"""Credits — a granted balance, spent to start a game (ADR-0016, AUTH-10).

This replaces layer 2 of ADR-0011, which was a *daily* allowance that regenerated at UTC midnight.
A balance does not regenerate: it is granted by an administrator, spent, and then gone. New
accounts hold zero, so nobody plays until someone says so.

**The charge is a single statement, not a read followed by a write.** `SELECT` the balance,
compare it, then `UPDATE` is the obvious shape and it is wrong: two requests from the same user
arriving together both read the old balance, both find room, and both proceed. That is not a
theoretical race — firing concurrent requests is exactly what someone trying to spend a credit
twice would do. Postgres decides it instead, via an `UPDATE ... WHERE credit_balance >= :cost`
whose `WHERE` clause *is* the check: a row comes back only if the update actually happened.

The same reasoning ran the game-count quota this supersedes; only the counter changed.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import CreditReason
from chessmark.db.models import CreditLedger, ModelRegistry, User


class InsufficientCreditsError(Exception):
    """Not enough credits to start this game.

    Carries both numbers so the API can say what it cost and what the caller holds — "insufficient
    credits" alone is a dead end for the person reading it, who cannot tell whether they are one
    short or twenty.
    """

    def __init__(self, *, needed: int, held: int) -> None:
        super().__init__(
            f"This game costs {needed} credit{'s' if needed != 1 else ''} and you have {held}."
        )
        self.needed = needed
        self.held = held


async def cost_of(session: AsyncSession, model_slugs: list[str]) -> int:
    """What a game against these models costs, in credits.

    A game costs the **sum of its seats** — two models means two prices added. A model missing
    from the registry costs the top tier rather than nothing: an unknown price is not a free one,
    and the caller is about to be refused for a bad slug anyway.
    """
    if not model_slugs:
        return 0

    rows = await session.scalars(
        sa.select(ModelRegistry).where(ModelRegistry.openrouter_id.in_(set(model_slugs)))
    )
    prices = {row.openrouter_id: row.credits for row in rows}

    from chessmark.agents.registry import TOP_TIER_CREDITS

    return sum(prices.get(slug, TOP_TIER_CREDITS) for slug in model_slugs)


def _entry(
    *,
    user_id: uuid.UUID,
    delta: int,
    balance_after: int,
    reason: CreditReason,
    game_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    note: str | None = None,
) -> CreditLedger:
    return CreditLedger(
        user_id=user_id,
        delta=delta,
        balance_after=balance_after,
        reason=reason,
        game_id=game_id,
        actor_user_id=actor_user_id,
        note=note,
    )


async def charge(
    session: AsyncSession,
    user_id: uuid.UUID,
    credits: int,
    *,
    game_id: uuid.UUID | None = None,
) -> CreditLedger | None:
    """Spend credits, or raise `InsufficientCreditsError`. Returns the ledger row it wrote.

    Charged *before* the game is created, not after. Counting on completion would let a user open
    any number of games at once and discover the price when the money was already committed — which
    is also why `game_id` is usually unknown here and set on the returned row once the game exists.
    Both happen in one transaction, so a failure anywhere rolls back the charge with it.

    A charge of zero — a game with no machine seat — succeeds without touching anything, and writes
    no row: a ledger of no-ops is a ledger nobody reads.
    """
    if credits <= 0:
        return None

    statement = (
        sa.update(User)
        .where(User.id == user_id, User.credit_balance >= credits)
        .values(credit_balance=User.credit_balance - credits)
        .returning(User.credit_balance)
    )

    remaining = (await session.execute(statement)).scalar_one_or_none()
    if remaining is None:
        raise InsufficientCreditsError(needed=credits, held=await balance_of(session, user_id))

    entry = _entry(
        user_id=user_id,
        delta=-credits,
        balance_after=int(remaining),
        reason=CreditReason.GAME_START,
        game_id=game_id,
    )
    session.add(entry)
    return entry


async def grant(
    session: AsyncSession,
    user_id: uuid.UUID,
    credits: int,
    *,
    actor_user_id: uuid.UUID | None = None,
    note: str | None = None,
    reason: CreditReason | None = None,
) -> int:
    """Add credits to a balance and return the new total (AUTH-11, AUTH-13).

    Also used to take them away, with a negative amount — clamped at zero, because a negative
    balance would have to be worked off before play resumed, which is a debt rather than a
    revocation and not what anyone means by removing credits.

    **The clamp is why `balance_after` is recorded rather than derived.** Revoking 10 from a
    balance of 2 moves it by 2, not 10, so a ledger that stored only the requested delta would not
    sum to the balance. The row records what actually happened.
    """
    before = await balance_of(session, user_id)

    statement = (
        sa.update(User)
        .where(User.id == user_id)
        .values(credit_balance=sa.func.greatest(User.credit_balance + credits, 0))
        .returning(User.credit_balance)
    )

    total = (await session.execute(statement)).scalar_one_or_none()
    if total is None:
        raise LookupError(f"no user with id {user_id}")

    after = int(total)
    if after != before:
        session.add(
            _entry(
                user_id=user_id,
                delta=after - before,
                balance_after=after,
                reason=reason
                or (CreditReason.ADMIN_GRANT if credits > 0 else CreditReason.ADMIN_REVOKE),
                actor_user_id=actor_user_id,
                note=note,
            )
        )
    return after


async def refund(
    session: AsyncSession, user_id: uuid.UUID, credits: int, *, note: str | None = None
) -> int:
    """Give credits back for a game that never ran.

    Distinct from `grant` only in the reason it records, and that is the point: a refund is an
    accident being undone, a grant is a decision about a person. Anyone auditing a balance needs
    to tell them apart.
    """
    return await grant(session, user_id, credits, note=note, reason=CreditReason.REFUND)


async def history_of(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 100
) -> list[CreditLedger]:
    """A balance's history, newest first."""
    rows = await session.scalars(
        sa.select(CreditLedger)
        .where(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.id.desc())
        .limit(limit)
    )
    return list(rows)


async def ledger_total(session: AsyncSession, user_id: uuid.UUID) -> int:
    """What the history says the balance should be.

    `users.credit_balance` is the enforcement point and this is the account of it; they must agree,
    and a test replays every ledger to prove it.
    """
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(CreditLedger.delta), 0)).where(
            CreditLedger.user_id == user_id
        )
    )
    return int(total or 0)


async def balance_of(session: AsyncSession, user_id: uuid.UUID) -> int:
    balance = await session.scalar(sa.select(User.credit_balance).where(User.id == user_id))
    return int(balance or 0)

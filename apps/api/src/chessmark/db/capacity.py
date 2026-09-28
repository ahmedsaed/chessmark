"""How much credit can be sold right now, and holding it while a checkout is open (ADR-0056).

Our OpenRouter account is prepaid, and every dollar of Chessmark credit is a promise of OpenRouter
usage. So credit is sold only while OpenRouter's balance covers all of it:

    headroom = OpenRouter remaining
             - credit users already hold      (positive balances: sold or granted, not yet spent)
             - credit held by open checkouts  (reservations not yet paid or expired)
             - the house reserve              (our own tournaments, and turns in flight)

**Two buyers must not both take the last of it.** `reserve` computes the headroom and inserts the
reservation under one transaction-scoped advisory lock, so the second buyer's check runs after the
first's reservation exists and counts it. The lock is held for two aggregates and an insert — never
across the call to Paddle, which happens after the commit.

**A paid purchase is always credited**, even if it pushes the headroom below zero — its reservation
expired while the buyer was still in the checkout, say. The money has moved; refusing the credit
would be the worse failure. `oversold` reports it so the operator tops OpenRouter up.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.credit_pricing import MAX_USD, MIN_USD, Quote, quote
from chessmark.db.enums import ReservationStatus
from chessmark.db.models import CreditReservation, User

#: How long a checkout holds its credit. Long enough to type a card; an abandoned one frees its
#: share of the headroom this long after it was opened.
HOLD = dt.timedelta(minutes=30)

#: The advisory lock every reservation takes. An arbitrary constant, used for nothing else.
LOCK_KEY = 0x0C4ED17


@dataclass(frozen=True, slots=True)
class Headroom:
    openrouter_remaining: Decimal
    held: Decimal
    reserved: Decimal
    house_reserve: Decimal

    @property
    def available(self) -> Decimal:
        return self.openrouter_remaining - self.held - self.reserved - self.house_reserve


class NoHeadroomError(Exception):
    """The amount asked for would sell credit OpenRouter's balance cannot cover."""

    def __init__(self, largest: Decimal | None) -> None:
        self.largest = largest
        super().__init__(
            f"Only up to ${largest} can be bought right now."
            if largest is not None
            else "Credit is sold out for now. Please try again later."
        )


async def headroom(
    session: AsyncSession,
    *,
    openrouter_remaining: Decimal,
    house_reserve: Decimal,
    now: dt.datetime | None = None,
) -> Headroom:
    """The figures behind what can be sold. Two statements."""
    now = now or dt.datetime.now(dt.UTC)
    held = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(sa.func.greatest(User.balance_usd, 0)), 0))
    )
    reserved = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(CreditReservation.credit_usd), 0)).where(
            CreditReservation.status == ReservationStatus.OPEN,
            CreditReservation.expires_at > now,
        )
    )
    return Headroom(
        openrouter_remaining=openrouter_remaining,
        held=Decimal(held or 0),
        reserved=Decimal(reserved or 0),
        house_reserve=house_reserve,
    )


def largest_affordable(available: Decimal) -> Decimal | None:
    """The biggest whole-dollar purchase whose credit fits, or `None` if not even the smallest."""
    amount = MAX_USD
    while amount >= MIN_USD:
        if quote(amount).credit_usd <= available:
            return amount
        amount -= 1
    return None


async def reserve(
    session: AsyncSession,
    user_id: uuid.UUID,
    q: Quote,
    *,
    openrouter_remaining: Decimal,
    house_reserve: Decimal,
    now: dt.datetime | None = None,
) -> CreditReservation:
    """Hold `q`'s credit for a checkout, or raise `NoHeadroomError`. The caller commits, which is
    what releases the lock."""
    now = now or dt.datetime.now(dt.UTC)
    await session.execute(sa.select(sa.func.pg_advisory_xact_lock(LOCK_KEY)))
    room = await headroom(
        session, openrouter_remaining=openrouter_remaining, house_reserve=house_reserve, now=now
    )
    if q.credit_usd > room.available:
        raise NoHeadroomError(largest_affordable(room.available))
    reservation = CreditReservation(
        user_id=user_id,
        price_usd=q.price_usd,
        credit_usd=q.credit_usd,
        status=ReservationStatus.OPEN,
        expires_at=now + HOLD,
    )
    session.add(reservation)
    await session.flush()
    return reservation


__all__ = [
    "HOLD",
    "Headroom",
    "NoHeadroomError",
    "headroom",
    "largest_affordable",
    "reserve",
]

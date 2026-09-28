"""Crediting what was bought through Paddle, and taking it back when it is refunded (ADR-0055).

Everything here is driven by Paddle's signed webhooks, never by the buyer's browser, and every
entry point is **idempotent on Paddle's own id** — the transaction for a purchase, the adjustment
for a refund — enforced by a unique constraint rather than by a read-then-write, so two concurrent
deliveries of one event cannot both credit it.

**Who a purchase credits, and for what amount, comes from its reservation** (ADR-0056): the API
wrote both when it created the Paddle transaction itself, with the reservation's id in the
transaction's `custom_data`. Nothing a browser sends reaches this module. The webhook checks that
what was paid is what was reserved; the amount includes any tax, so the credit is then settled
from the tax Paddle actually charged. A purchase that does not match is recorded and not credited.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.credit_pricing import AmountError, quote
from chessmark.db.credits import move_for_purchase
from chessmark.db.enums import CreditReason, PurchaseStatus, ReservationStatus
from chessmark.db.models import (
    CreditLedger,
    CreditReservation,
    Purchase,
    PurchaseAdjustment,
)

log = logging.getLogger(__name__)

#: The keys the API puts in each transaction's `custom_data` (`routes/credit.py`). The reservation
#: is what is trusted; the Clerk id is for a person reading Paddle's dashboard.
RESERVATION_KEY = "reservation_id"
USER_KEY = "clerk_user_id"

#: Adjustments that take a purchase's credit back, and the one that restores it.
TAKES_BACK = {"refund": CreditReason.PURCHASE_REFUNDED, "chargeback": CreditReason.CHARGEBACK}
RESTORES = {"chargeback_reverse": CreditReason.CHARGEBACK_REVERSED}

#: `users.balance_usd`'s scale.
PLACES = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class Recorded:
    purchase: Purchase
    #: False when this delivery was a repeat of one already recorded.
    new: bool


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _price_id(items: Any) -> str | None:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        price = items[0].get("price")
        if isinstance(price, dict):
            return _text(price.get("id"))
    return None


def _cents(usd: Decimal) -> str:
    return str(int((usd * 100).to_integral_value()))


async def _reservation_for(
    session: AsyncSession, transaction_id: str, custom: Any
) -> CreditReservation | None:
    """The transaction's reservation: by the id we put in its `custom_data`, else by the
    transaction id we stored on it once Paddle created the transaction."""
    raw = custom.get(RESERVATION_KEY) if isinstance(custom, dict) else None
    try:
        reservation_id = uuid.UUID(str(raw)) if raw else None
    except ValueError:
        reservation_id = None
    if reservation_id is not None:
        condition = CreditReservation.id == reservation_id
    else:
        condition = CreditReservation.paddle_transaction_id == transaction_id
    found: CreditReservation | None = await session.scalar(
        sa.select(CreditReservation).where(condition).with_for_update()
    )
    return found


def _mismatch(
    reservation: CreditReservation | None,
    transaction_id: str,
    total: str | None,
    currency: str | None,
) -> str | None:
    if reservation is None:
        return "no reservation matches this transaction"
    if reservation.paddle_transaction_id not in (None, transaction_id):
        return f"its reservation belongs to {reservation.paddle_transaction_id}"
    if reservation.status is ReservationStatus.CONSUMED:
        return "its reservation was already paid for"
    # The transaction is created in dollars; 3700 of any other currency is not $37.
    if currency != "USD":
        return f"paid in {currency}, not USD"
    if total != _cents(reservation.price_usd):
        return f"paid {total} cents, reserved {_cents(reservation.price_usd)}"
    return None


async def record_transaction(session: AsyncSession, data: dict[str, Any]) -> Recorded | None:
    """Record a completed Paddle transaction and credit its buyer what was reserved, once.

    Returns `None` for a payload that is not a transaction at all (no id). The caller commits.
    """
    transaction_id = _text(data.get("id"))
    if transaction_id is None:
        return None

    existing = await session.scalar(
        sa.select(Purchase).where(Purchase.paddle_transaction_id == transaction_id)
    )
    if existing is not None:
        return Recorded(purchase=existing, new=False)

    details = data.get("details")
    totals = details.get("totals") if isinstance(details, dict) else None
    totals = totals if isinstance(totals, dict) else {}

    reservation = await _reservation_for(session, transaction_id, data.get("custom_data"))
    currency = _text(data.get("currency_code")) or _text(totals.get("currency_code"))
    problem = _mismatch(reservation, transaction_id, _text(totals.get("total")), currency)
    user_id = reservation.user_id if reservation is not None else None
    credit = Decimal(0)
    if reservation is not None and not problem:
        # The amount paid includes any tax, so the credit is settled here, from the tax Paddle
        # actually charged — not from the checkout's quote, which could only estimate it. The
        # reservation held the no-tax maximum, so this is never more than was reserved.
        try:
            tax = Decimal(_text(totals.get("tax")) or "0") / 100
            credit = quote(reservation.price_usd, tax).credit_usd
        except (ArithmeticError, AmountError):
            problem = f"Paddle reported a tax of {totals.get('tax')!r} cents"

    inserted = await session.scalar(
        insert(Purchase)
        .values(
            paddle_transaction_id=transaction_id,
            user_id=user_id,
            reservation_id=reservation.id if reservation is not None else None,
            status=PurchaseStatus.UNMATCHED if problem else PurchaseStatus.CREDITED,
            problem=problem,
            paddle_price_id=_price_id(data.get("items")),
            paddle_customer_id=_text(data.get("customer_id")),
            credit_usd=credit,
            currency_code=currency or "",
            grand_total=_text(totals.get("grand_total")) or _text(totals.get("total")) or "0",
            tax=_text(totals.get("tax")),
            fee=_text(totals.get("fee")),
            earnings=_text(totals.get("earnings")),
        )
        .on_conflict_do_nothing(index_elements=[Purchase.paddle_transaction_id])
        .returning(Purchase)
    )
    if inserted is None:
        # A concurrent delivery of the same event won the constraint and credited it.
        raced = await session.scalar(
            sa.select(Purchase).where(Purchase.paddle_transaction_id == transaction_id)
        )
        assert raced is not None
        return Recorded(purchase=raced, new=False)

    if problem:
        log.warning("paddle transaction %s not credited: %s", transaction_id, problem)
        return Recorded(purchase=inserted, new=True)

    assert reservation is not None and user_id is not None
    # Consumed in the same transaction as the credit, so the headroom never counts this credit
    # twice (held and reserved) or not at all.
    reservation.status = ReservationStatus.CONSUMED
    reservation.paddle_transaction_id = transaction_id
    await move_for_purchase(
        session, user_id, credit, purchase_id=inserted.id, reason=CreditReason.PURCHASE
    )
    return Recorded(purchase=inserted, new=True)


async def apply_adjustment(
    session: AsyncSession, data: dict[str, Any]
) -> PurchaseAdjustment | None:
    """Apply an approved refund, chargeback or chargeback reversal to its purchase's credit, once.

    **What is taken back is in proportion to what was refunded**: a full refund takes the pack's
    whole credit, half of the money takes half of it. Never more than the purchase granted, however
    refunds and chargebacks on one purchase add up. Anything not yet approved — a refund awaiting
    Paddle's review — and any other kind of adjustment is ignored; `None` says so. The caller commits.
    """
    adjustment_id = _text(data.get("id"))
    action = _text(data.get("action"))
    reason = TAKES_BACK.get(action or "") or RESTORES.get(action or "")
    if adjustment_id is None or reason is None or data.get("status") != "approved":
        return None

    transaction_id = _text(data.get("transaction_id"))
    purchase = (
        await session.scalar(
            sa.select(Purchase)
            .where(Purchase.paddle_transaction_id == transaction_id)
            .with_for_update()
        )
        if transaction_id
        else None
    )
    if purchase is None:
        log.warning("paddle adjustment %s names no purchase we hold", adjustment_id)
        return None

    totals = data.get("totals")
    amount = _text(totals.get("total")) if isinstance(totals, dict) else None
    try:
        share = Decimal(amount or "0") / Decimal(purchase.grand_total)
    except (ArithmeticError, ValueError):
        share = Decimal(0)
    share = min(max(share, Decimal(0)), Decimal(1))

    # What earlier adjustments already did, so the sum never takes back more than was granted.
    already = Decimal(
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(PurchaseAdjustment.credit_delta_usd), 0)).where(
                PurchaseAdjustment.purchase_id == purchase.id
            )
        )
        or 0
    )
    wanted = (purchase.credit_usd * share).quantize(PLACES)
    if action in TAKES_BACK:
        delta = -min(wanted, max(purchase.credit_usd + already, Decimal(0)))
    else:
        delta = min(wanted, max(-already, Decimal(0)))

    recorded = await session.scalar(
        insert(PurchaseAdjustment)
        .values(
            paddle_adjustment_id=adjustment_id,
            purchase_id=purchase.id,
            action=action,
            amount=amount or "0",
            credit_delta_usd=delta,
        )
        .on_conflict_do_nothing(index_elements=[PurchaseAdjustment.paddle_adjustment_id])
        .returning(PurchaseAdjustment)
    )
    if recorded is None:
        return None  # already applied by an earlier delivery

    if purchase.user_id is not None and purchase.status is PurchaseStatus.CREDITED:
        await move_for_purchase(
            session, purchase.user_id, delta, purchase_id=purchase.id, reason=reason
        )
    return recorded


#: The refund policy's window for an untouched purchase (`/refunds`).
REFUND_WINDOW = dt.timedelta(days=14)

#: What counts as spending a purchase: a turn's charge, or a settlement that charged more.
SPENDING = (CreditReason.TURN, CreditReason.SETTLEMENT)


@dataclass(frozen=True, slots=True)
class PurchaseReport:
    """One purchase, and whether the refund policy lets it be refunded (`./chessmark purchases`)."""

    purchase: Purchase
    #: Credit charged to the account after this purchase — any at all makes it "touched".
    spent_since: Decimal
    #: What refunds and chargebacks have already done to its credit (zero or negative).
    adjusted: Decimal
    refundable: bool
    why: str
    window_ends: dt.datetime


def _judge(
    purchase: Purchase, spent: Decimal, adjusted: Decimal, now: dt.datetime
) -> tuple[bool, str]:
    """The refund policy, as a rule an operator can read the answer of. Money comes back only for
    a purchase that is credited, not already refunded, inside the window, and untouched since."""
    if purchase.status is not PurchaseStatus.CREDITED:
        return False, "never credited (unmatched): settle it by hand"
    if adjusted != 0:
        return False, "already refunded or charged back"
    if now > purchase.created_at + REFUND_WINDOW:
        return False, "more than 14 days ago"
    if spent > 0:
        return False, "credit was spent after it"
    return True, "untouched and inside 14 days"


async def purchases_of(
    session: AsyncSession, user_id: uuid.UUID, *, now: dt.datetime | None = None
) -> list[PurchaseReport]:
    """Every purchase of this account, oldest first, with whether each may be refunded.

    **"Untouched" is judged on the pooled balance**, because credit is not tracked per purchase:
    a purchase is untouched when nothing was charged to the account after it was made. So of two
    purchases made before one game, neither is; a purchase made after the game still is.

    One statement whatever the count: what was spent after each purchase and what adjustments did
    to it are correlated subqueries, not a read per purchase.
    """
    now = now or dt.datetime.now(dt.UTC)
    spent = (
        sa.select(sa.func.coalesce(sa.func.sum(-CreditLedger.delta), 0))
        .where(
            CreditLedger.user_id == Purchase.user_id,
            CreditLedger.reason.in_(SPENDING),
            CreditLedger.delta < 0,
            CreditLedger.created_at > Purchase.created_at,
        )
        .correlate(Purchase)
        .scalar_subquery()
    )
    adjusted = (
        sa.select(sa.func.coalesce(sa.func.sum(PurchaseAdjustment.credit_delta_usd), 0))
        .where(PurchaseAdjustment.purchase_id == Purchase.id)
        .correlate(Purchase)
        .scalar_subquery()
    )
    rows = await session.execute(
        sa.select(Purchase, spent, adjusted)
        .where(Purchase.user_id == user_id)
        .order_by(Purchase.created_at, Purchase.id)
    )
    reports = []
    for purchase, spent_since, adjusted_by in rows.all():
        spent_since, adjusted_by = Decimal(spent_since), Decimal(adjusted_by)
        refundable, why = _judge(purchase, spent_since, adjusted_by, now)
        reports.append(
            PurchaseReport(
                purchase=purchase,
                spent_since=spent_since,
                adjusted=adjusted_by,
                refundable=refundable,
                why=why,
                window_ends=purchase.created_at + REFUND_WINDOW,
            )
        )
    return reports


__all__ = [
    "REFUND_WINDOW",
    "RESERVATION_KEY",
    "USER_KEY",
    "PurchaseReport",
    "Recorded",
    "apply_adjustment",
    "purchases_of",
    "record_transaction",
]

"""Crediting what was bought through Paddle, and taking it back when it is refunded (ADR-0055).

Everything here is driven by Paddle's signed webhooks, never by the buyer's browser, and every
entry point is **idempotent on Paddle's own id** — the transaction for a purchase, the adjustment
for a refund — enforced by a unique constraint rather than by a read-then-write, so two concurrent
deliveries of one event cannot both credit it.

What a pack grants comes from `core.credit_packs`, keyed by the price Paddle says was paid for.
Which account it goes to comes from the checkout's `custom_data`, which the browser set: that is
the one buyer-supplied value trusted here, and the worst it can do is credit a pack somebody paid
for to a different account than their own — a gift, not a theft.
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

from chessmark.core.credit_packs import Pack
from chessmark.db.credits import move_for_purchase
from chessmark.db.enums import CreditReason, PurchaseStatus
from chessmark.db.models import CreditLedger, Purchase, PurchaseAdjustment, User

log = logging.getLogger(__name__)

#: The key the checkout puts the buyer's Clerk id under. The web tier writes it (`BuyCredit`).
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


def _pack_credit(items: Any, packs: dict[str, Pack]) -> tuple[Decimal, str | None, str | None]:
    """What a transaction's items grant, the price it names, and why it cannot be credited.

    Every line must be a pack. A transaction with a line we do not recognise is not credited at all
    rather than credited in part: a checkout carrying a foreign price is either a misconfiguration
    or somebody composing their own, and an operator should look at it either way.
    """
    if not isinstance(items, list) or not items:
        return Decimal(0), None, "the transaction has no items"
    total = Decimal(0)
    price_ids: list[str] = []
    for item in items:
        price = item.get("price") if isinstance(item, dict) else None
        price_id = _text(price.get("id")) if isinstance(price, dict) else None
        quantity = item.get("quantity") if isinstance(item, dict) else None
        if price_id is None or not isinstance(quantity, int) or quantity < 1:
            return Decimal(0), None, "an item has no price or quantity"
        price_ids.append(price_id)
        pack = packs.get(price_id)
        if pack is None:
            return Decimal(0), price_id, f"{price_id} is not a credit pack"
        total += pack.credit_usd * quantity
    return total, ",".join(price_ids), None


async def record_transaction(
    session: AsyncSession, data: dict[str, Any], packs: dict[str, Pack]
) -> Recorded | None:
    """Record a completed Paddle transaction and credit its buyer, once.

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

    credit, price_id, problem = _pack_credit(data.get("items"), packs)

    custom = data.get("custom_data")
    clerk_id = _text(custom.get(USER_KEY)) if isinstance(custom, dict) else None
    user_id = None
    if clerk_id is not None:
        user_id = await session.scalar(sa.select(User.id).where(User.clerk_user_id == clerk_id))
    if problem is None and user_id is None:
        problem = "no account matches the checkout's user" if clerk_id else "no user in custom_data"

    details = data.get("details")
    totals = details.get("totals") if isinstance(details, dict) else None
    totals = totals if isinstance(totals, dict) else {}

    inserted = await session.scalar(
        insert(Purchase)
        .values(
            paddle_transaction_id=transaction_id,
            user_id=user_id,
            status=PurchaseStatus.UNMATCHED if problem else PurchaseStatus.CREDITED,
            problem=problem,
            paddle_price_id=price_id,
            paddle_customer_id=_text(data.get("customer_id")),
            credit_usd=Decimal(0) if problem else credit,
            currency_code=_text(data.get("currency_code"))
            or _text(totals.get("currency_code"))
            or "",
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
    else:
        assert user_id is not None
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
    "USER_KEY",
    "PurchaseReport",
    "Recorded",
    "apply_adjustment",
    "purchases_of",
    "record_transaction",
]

"""Buying credit: what an amount costs and grants, the checkout, and Paddle's webhook (ADR-0055,
ADR-0056).

The buyer chooses a whole number of dollars. `POST /credit/checkout` quotes it, **reserves** its
credit against what OpenRouter's balance can cover, and creates the Paddle transaction at that
price; the browser opens Paddle's checkout on it. Credit moves only when Paddle's signed
`transaction.completed` reaches `POST /webhooks/paddle`, for what the reservation says, and the
page then asks `GET /credit/purchases/{id}` until the purchase it just made shows up.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from chessmark.api.deps import BalanceDep, CurrentUser, PaddleApiDep, SessionDep, SettingsDep
from chessmark.core.credit_pricing import (
    MAX_USD,
    MIN_USD,
    PRESETS,
    AmountError,
    Quote,
    checked_amount,
    quote,
)
from chessmark.core.paddle_api import PaddleApiError
from chessmark.core.paddle_signature import WebhookError, verify
from chessmark.db.capacity import NoHeadroomError, reserve
from chessmark.db.credits import balance_of
from chessmark.db.enums import PurchaseStatus, ReservationStatus
from chessmark.db.models import Purchase
from chessmark.db.purchases import (
    RESERVATION_KEY,
    USER_KEY,
    apply_adjustment,
    record_transaction,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["credit"])


class QuoteOut(BaseModel):
    """A purchase as the sum the page lays out. The page does none of this arithmetic."""

    price_usd: Decimal
    processor_fee_usd: Decimal
    upkeep_usd: Decimal
    provider_fee_usd: Decimal
    credit_usd: Decimal

    @classmethod
    def of(cls, q: Quote) -> QuoteOut:
        return cls(
            price_usd=q.price_usd,
            processor_fee_usd=q.processor_fee_usd,
            upkeep_usd=q.upkeep_usd,
            provider_fee_usd=q.provider_fee_usd,
            credit_usd=q.credit_usd,
        )


class OptionsOut(BaseModel):
    #: False until Paddle and OpenRouter's management key are configured. The amounts and their
    #: breakdowns are public either way — what credit costs is readable before it is on sale.
    selling: bool
    min_usd: Decimal
    max_usd: Decimal
    presets: list[QuoteOut]


class CheckoutIn(BaseModel):
    amount_usd: Decimal


class CheckoutOut(BaseModel):
    #: The Paddle transaction to open the checkout on.
    transaction_id: str
    quote: QuoteOut


class PurchaseOut(BaseModel):
    status: PurchaseStatus
    credit_usd: Decimal
    balance_usd: Decimal


@router.get("/credit/options", response_model=OptionsOut)
async def credit_options(settings: SettingsDep) -> OptionsOut:
    """The range, the one-click amounts and their breakdowns. No query: pure configuration."""
    return OptionsOut(
        selling=settings.selling_credit,
        min_usd=MIN_USD,
        max_usd=MAX_USD,
        presets=[QuoteOut.of(quote(amount)) for amount in PRESETS],
    )


@router.get("/credit/quote", response_model=QuoteOut)
async def credit_quote(amount: Annotated[str, Query()]) -> QuoteOut:
    """The breakdown of any amount a buyer types. 422 with the reason for one that cannot be bought."""
    try:
        return QuoteOut.of(quote(checked_amount(amount)))
    except AmountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.post("/credit/checkout", response_model=CheckoutOut)
async def start_checkout(
    body: CheckoutIn,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser,
    balance: BalanceDep,
    paddle: PaddleApiDep,
) -> CheckoutOut:
    """Reserve an amount's credit, and create the Paddle transaction to pay for it.

    **Refused rather than oversold.** If OpenRouter's balance cannot cover the credit on top of
    what users already hold and what open checkouts reserve, the answer is 409 with the largest
    amount that can be bought now. If OpenRouter will not say what its balance is, nothing is sold
    until it does (503): an unknown balance is not a large one.

    The reservation commits before Paddle is called, so the lock that orders concurrent buyers is
    never held across a network call; if Paddle then refuses, the reservation is released.
    """
    if not settings.selling_credit:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Credit is not on sale yet."
        )
    try:
        q = quote(checked_amount(body.amount_usd))
    except AmountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

    remaining = await balance.remaining()
    if remaining is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Buying credit is paused for a moment. Please try again shortly.",
        )
    try:
        reservation = await reserve(
            session,
            user.id,
            q,
            openrouter_remaining=remaining,
            house_reserve=Decimal(str(settings.credit_reserve_usd)),
        )
    except NoHeadroomError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    reservation_id = reservation.id
    await session.commit()

    try:
        transaction_id = await paddle.create_transaction(
            product_id=settings.paddle_product_id,
            price_usd=q.price_usd,
            credit_usd=q.credit_usd,
            custom_data={RESERVATION_KEY: str(reservation_id), USER_KEY: user.clerk_user_id},
        )
    except PaddleApiError as error:
        reservation.status = ReservationStatus.RELEASED
        await session.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="The checkout could not be started. Nothing was charged; please try again.",
        ) from error

    reservation.paddle_transaction_id = transaction_id
    await session.commit()
    return CheckoutOut(transaction_id=transaction_id, quote=QuoteOut.of(q))


@router.get("/credit/purchases/{transaction_id}", response_model=PurchaseOut)
async def get_purchase(transaction_id: str, session: SessionDep, user: CurrentUser) -> PurchaseOut:
    """One of the caller's purchases, once Paddle's webhook has recorded it.

    404 until then — and for anybody else's purchase, so the answer does not confirm that a
    transaction id someone guessed exists.
    """
    purchase = await session.scalar(
        sa.select(Purchase).where(
            Purchase.paddle_transaction_id == transaction_id, Purchase.user_id == user.id
        )
    )
    if purchase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not recorded yet.")
    return PurchaseOut(
        status=purchase.status,
        credit_usd=purchase.credit_usd,
        balance_usd=await balance_of(session, user.id),
    )


@router.post("/webhooks/paddle", status_code=status.HTTP_200_OK)
async def paddle_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    paddle_signature: Annotated[str | None, Header(alias="paddle-signature")] = None,
) -> dict[str, bool]:
    """Handle a Paddle notification.

    **Only a 2xx stops Paddle retrying**, for three days on live. So a delivery we cannot verify is
    refused (Paddle retries, which recovers a rotated secret once it is deployed), and one we
    verified but do not act on is acknowledged — refusing an event we will never handle would retry
    it for three days to no end. A purchase that cannot be matched is acknowledged too: its money
    has moved, it is recorded as unmatched, and an operator settles it.
    """
    body = await request.body()
    try:
        verify(settings.paddle_webhook_secret, body=body, signature_header=paddle_signature)
    except WebhookError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature."
        ) from error

    try:
        payload: Any = json.loads(body)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not JSON.") from error
    event = payload.get("event_type") if isinstance(payload, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {"received": True}

    if event == "transaction.completed":
        recorded = await record_transaction(session, data)
        await session.commit()
        if recorded is not None and recorded.new:
            log.info(
                "paddle transaction %s: %s",
                recorded.purchase.paddle_transaction_id,
                recorded.purchase.status,
            )
    elif event in {"adjustment.created", "adjustment.updated"}:
        applied = await apply_adjustment(session, data)
        await session.commit()
        if applied is not None:
            log.info(
                "paddle adjustment %s (%s): %s",
                applied.paddle_adjustment_id,
                applied.action,
                applied.credit_delta_usd,
            )

    return {"received": True}

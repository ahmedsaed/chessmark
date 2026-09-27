"""Buying credit: the packs on sale, a purchase's status, and Paddle's webhook (ADR-0055).

The browser opens Paddle's checkout and never touches a balance. Credit moves only when Paddle's
signed `transaction.completed` reaches `POST /webhooks/paddle`; the page then asks
`GET /credit/purchases/{id}` until the purchase it just made shows up.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from chessmark.api.deps import CurrentUser, SessionDep, SettingsDep
from chessmark.core.config import Settings
from chessmark.core.credit_packs import PACK_PRICES, Pack, credit_for, parse_packs
from chessmark.core.paddle_signature import WebhookError, verify
from chessmark.db.credits import balance_of
from chessmark.db.enums import PurchaseStatus
from chessmark.db.models import Purchase
from chessmark.db.purchases import apply_adjustment, record_transaction

log = logging.getLogger(__name__)

router = APIRouter(tags=["credit"])


class PackOut(BaseModel):
    #: The Paddle price to check out with; `None` while selling is off.
    price_id: str | None
    price_usd: Decimal
    #: What comes out of the price, so the page can show the sum without doing any of it.
    processor_fee_usd: Decimal
    upkeep_usd: Decimal
    credit_usd: Decimal


class PacksOut(BaseModel):
    #: False until Paddle is configured on this server. The packs are listed either way — what
    #: credit costs is public before it is on sale, which is also what a payment processor's review
    #: looks for — and the page offers no checkout until this is true.
    selling: bool
    packs: list[PackOut]


class PurchaseOut(BaseModel):
    status: PurchaseStatus
    credit_usd: Decimal
    balance_usd: Decimal


def _packs(settings: Settings) -> dict[str, Pack]:
    if not settings.selling_credit:
        return {}
    return {pack.price_id: pack for pack in parse_packs(settings.paddle_price_ids)}


@router.get("/credit/packs", response_model=PacksOut)
async def list_packs(settings: SettingsDep) -> PacksOut:
    """The packs and what each grants, and whether they can be bought. No query: it is read from
    configuration."""
    on_sale = sorted(_packs(settings).values(), key=lambda pack: pack.price_usd)
    packs = on_sale or [
        Pack(price_id="", price_usd=price, credit_usd=credit_for(price)) for price in PACK_PRICES
    ]
    return PacksOut(
        selling=bool(on_sale),
        packs=[
            PackOut(
                price_id=p.price_id or None,
                price_usd=p.price_usd,
                processor_fee_usd=p.processor_fee_usd,
                upkeep_usd=p.upkeep_usd,
                credit_usd=p.credit_usd,
            )
            for p in packs
        ],
    )


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
        recorded = await record_transaction(session, data, _packs(settings))
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

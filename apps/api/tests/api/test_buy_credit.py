"""Buying credit through Paddle, from its signed webhook to the buyer's balance (ADR-0055).

Driven through the HTTP endpoint with deliveries signed the way Paddle signs them, because the rules
that matter live across the boundary: a purchase credits once however often Paddle delivers it, a
delivery we cannot verify moves nothing, and a refund takes back what the purchase granted and no
more — even below zero.
"""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.config import Settings, get_settings
from chessmark.core.paddle_signature import sign
from chessmark.db.enums import CreditReason, PurchaseStatus
from chessmark.db.models import CreditLedger, Purchase, User
from tests.api.conftest import as_user

pytestmark = pytest.mark.integration

SECRET = "pdl_ntfset_test"
PRICES = {"5": "pri_05test", "10": "pri_10test", "25": "pri_25test"}
BUYER = "user_buyer"


@pytest.fixture
def selling(app: FastAPI) -> None:
    settings = Settings(
        paddle_webhook_secret=SECRET,
        paddle_price_ids=",".join(f"{usd}={pid}" for usd, pid in PRICES.items()),
    )
    app.dependency_overrides[get_settings] = lambda: settings


async def _buyer(db: AsyncSession, clerk_id: str = BUYER) -> User:
    user = User(clerk_user_id=clerk_id, email=f"{clerk_id}@chessmark.test")
    db.add(user)
    await db.commit()
    return user


def _transaction(
    txn: str = "txn_1", *, price: str = PRICES["10"], clerk_id: str | None = BUYER
) -> dict[str, Any]:
    return {
        "event_type": "transaction.completed",
        "data": {
            "id": txn,
            "status": "completed",
            "customer_id": "ctm_1",
            "currency_code": "USD",
            "custom_data": {"clerk_user_id": clerk_id} if clerk_id else None,
            "items": [{"price": {"id": price}, "quantity": 1}],
            "details": {
                "totals": {
                    "currency_code": "USD",
                    "grand_total": "1000",
                    "tax": "0",
                    "fee": "100",
                    "earnings": "900",
                }
            },
        },
    }


def _adjustment(
    adj: str, action: str, *, total: str = "1000", status: str = "approved", txn: str = "txn_1"
) -> dict[str, Any]:
    return {
        "event_type": "adjustment.updated" if status == "approved" else "adjustment.created",
        "data": {
            "id": adj,
            "action": action,
            "status": status,
            "transaction_id": txn,
            "totals": {"total": total, "currency_code": "USD"},
        },
    }


async def _deliver(client: AsyncClient, event: dict[str, Any], *, secret: str = SECRET) -> Response:
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    return await client.post(
        "/webhooks/paddle",
        content=body,
        headers={
            "content-type": "application/json",
            "paddle-signature": f"ts={ts};h1={sign(secret, timestamp=ts, body=body)}",
        },
    )


async def _balance(db: AsyncSession, clerk_id: str = BUYER) -> Decimal:
    db.expire_all()
    return Decimal(
        await db.scalar(sa.select(User.balance_usd).where(User.clerk_user_id == clerk_id)) or 0
    )


async def test_a_completed_purchase_credits_its_pack_once(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)

    for _ in range(3):  # Paddle delivers at least once, and retries what it did not see acked
        assert (await _deliver(client, _transaction())).status_code == 200

    assert await _balance(db) == Decimal("8.50")
    ledger = list(await db.scalars(sa.select(CreditLedger)))
    assert [(row.reason, row.delta) for row in ledger] == [(CreditReason.PURCHASE, Decimal("8.5"))]
    purchase = await db.scalar(sa.select(Purchase))
    assert purchase is not None and ledger[0].purchase_id == purchase.id
    assert (purchase.status, purchase.earnings, purchase.fee) == (
        PurchaseStatus.CREDITED,
        "900",
        "100",
    )


async def test_concurrent_deliveries_of_one_purchase_credit_it_once(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    """The unique transaction id, not a read-then-write, is what stops the second credit."""
    await _buyer(db)
    responses = await asyncio.gather(*(_deliver(client, _transaction()) for _ in range(5)))
    assert {r.status_code for r in responses} == {200}
    assert await _balance(db) == Decimal("8.50")


async def test_a_delivery_that_is_not_paddles_moves_nothing(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    response = await _deliver(client, _transaction(), secret="pdl_ntfset_forged")
    assert response.status_code == 401  # not 2xx: Paddle retries, which recovers a rotated secret
    assert await db.scalar(sa.select(sa.func.count()).select_from(Purchase)) == 0
    assert await _balance(db) == 0


async def test_a_price_that_is_not_a_pack_is_recorded_and_not_credited(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    assert (await _deliver(client, _transaction(price="pri_other"))).status_code == 200
    purchase = await db.scalar(sa.select(Purchase))
    assert purchase is not None and purchase.status is PurchaseStatus.UNMATCHED
    assert purchase.problem and "pri_other" in purchase.problem
    assert await _balance(db) == 0


async def test_a_purchase_with_no_matching_account_is_recorded_and_not_credited(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    for clerk_id in ("user_nobody", None):
        txn = f"txn_{clerk_id}"
        assert (await _deliver(client, _transaction(txn, clerk_id=clerk_id))).status_code == 200
    statuses = set(await db.scalars(sa.select(Purchase.status)))
    assert statuses == {PurchaseStatus.UNMATCHED}
    assert await _balance(db) == 0


async def test_a_refund_takes_the_credit_back_once_it_is_approved(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    await _deliver(client, _transaction())

    await _deliver(client, _adjustment("adj_1", "refund", status="pending_approval"))
    assert await _balance(db) == Decimal("8.50"), "a refund awaiting review has not happened"

    for _ in range(2):
        await _deliver(client, _adjustment("adj_1", "refund"))
    assert await _balance(db) == 0
    reasons = list(await db.scalars(sa.select(CreditLedger.reason).order_by(CreditLedger.id)))
    assert reasons == [CreditReason.PURCHASE, CreditReason.PURCHASE_REFUNDED]


async def test_a_partial_refund_takes_back_its_share(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    await _deliver(client, _transaction())
    await _deliver(client, _adjustment("adj_half", "refund", total="500"))
    assert await _balance(db) == Decimal("4.25")


async def test_a_chargeback_can_leave_a_spent_balance_below_zero_and_its_reversal_restores_it(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    """The refund policy says so: the credit was spent, and the money went back anyway."""
    user = await _buyer(db)
    await _deliver(client, _transaction())
    await db.execute(sa.update(User).where(User.id == user.id).values(balance_usd=Decimal("1")))
    await db.commit()

    await _deliver(client, _adjustment("adj_cb", "chargeback"))
    assert await _balance(db) == Decimal("-7.50")

    await _deliver(client, _adjustment("adj_cbr", "chargeback_reverse"))
    assert await _balance(db) == Decimal("1")


async def test_refunds_and_chargebacks_never_take_back_more_than_was_granted(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    await _deliver(client, _transaction())
    await _deliver(client, _adjustment("adj_r", "refund"))
    await _deliver(client, _adjustment("adj_cb", "chargeback"))
    assert await _balance(db) == 0


async def test_the_packs_on_sale_say_what_each_grants(client: AsyncClient, selling: None) -> None:
    body = (await client.get("/credit/packs")).json()
    assert body["selling"] is True
    assert [(p["price_usd"], p["credit_usd"], p["price_id"]) for p in body["packs"]] == [
        ("5", "4.00", PRICES["5"]),
        ("10", "8.50", PRICES["10"]),
        ("25", "22.00", PRICES["25"]),
    ]


async def test_nothing_is_on_sale_until_paddle_is_configured(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        paddle_webhook_secret="", paddle_price_ids=""
    )
    assert (await client.get("/credit/packs")).json() == {"selling": False, "packs": []}


async def test_a_buyer_can_see_their_own_purchase_and_nobody_elses(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    await _buyer(db)
    await _buyer(db, "user_other")

    mine = as_user(BUYER)
    assert (await client.get("/credit/purchases/txn_1", headers=mine)).status_code == 404

    await _deliver(client, _transaction())
    body = (await client.get("/credit/purchases/txn_1", headers=mine)).json()
    assert body == {"status": "credited", "credit_usd": "8.50000000", "balance_usd": "8.50000000"}

    other = as_user("user_other")
    assert (await client.get("/credit/purchases/txn_1", headers=other)).status_code == 404

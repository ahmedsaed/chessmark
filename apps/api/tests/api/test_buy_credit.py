"""Buying credit through Paddle, from its signed webhook to the buyer's balance (ADR-0055).

Driven through the HTTP endpoint with deliveries signed the way Paddle signs them, because the rules
that matter live across the boundary: a purchase credits once however often Paddle delivers it, a
delivery we cannot verify moves nothing, and a refund takes back what the purchase granted and no
more — even below zero.
"""

from __future__ import annotations

import asyncio
import datetime as dt
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
from chessmark.db.purchases import purchases_of
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
    rows = [
        (p["price_id"], p["price_usd"], p["processor_fee_usd"], p["upkeep_usd"], p["credit_usd"])
        for p in body["packs"]
    ]
    assert rows == [
        (PRICES["5"], "5", "0.75", "0.25", "4.00"),
        (PRICES["10"], "10", "1.00", "0.50", "8.50"),
        (PRICES["25"], "25", "1.75", "1.25", "22.00"),
    ]


async def test_the_packs_are_shown_but_not_sold_until_paddle_is_configured(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        paddle_webhook_secret="", paddle_price_ids=""
    )
    body = (await client.get("/credit/packs")).json()
    assert body["selling"] is False
    # Still listed, with nothing to check out with: the price is public before it is on sale.
    assert [(p["price_usd"], p["credit_usd"], p["price_id"]) for p in body["packs"]] == [
        ("5", "4.00", None),
        ("10", "8.50", None),
        ("25", "22.00", None),
    ]


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


async def _purchase_at(
    client: AsyncClient, db: AsyncSession, txn: str, price: str, when: dt.datetime
) -> None:
    await _deliver(client, _transaction(txn, price=price))
    await db.execute(
        sa.update(Purchase).where(Purchase.paddle_transaction_id == txn).values(created_at=when)
    )
    await db.commit()


async def _spend_at(db: AsyncSession, user: User, usd: str, when: dt.datetime) -> None:
    db.add(
        CreditLedger(
            user_id=user.id,
            delta=-Decimal(usd),
            balance_after=Decimal(0),
            reason=CreditReason.TURN,
            created_at=when,
        )
    )
    await db.commit()


async def test_a_purchase_is_refundable_only_while_untouched_and_inside_fourteen_days(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    """The refund policy on a pooled balance: of two purchases made before a game, neither is
    untouched; one made after the game still is, until its fourteen days run out."""
    user = await _buyer(db)
    now = dt.datetime.now(dt.UTC)
    day = dt.timedelta(days=1)
    await _purchase_at(client, db, "txn_a", PRICES["5"], now - 5 * day)
    await _purchase_at(client, db, "txn_b", PRICES["10"], now - 4 * day)
    await _spend_at(db, user, "0.30", now - 3 * day)
    await _purchase_at(client, db, "txn_c", PRICES["25"], now - 2 * day)
    await _purchase_at(client, db, "txn_d", PRICES["5"], now - 20 * day)
    await _purchase_at(client, db, "txn_e", PRICES["5"], now - 1 * day)
    await _deliver(client, _adjustment("adj_e", "refund", txn="txn_e", total="1000"))

    user_id = user.id
    db.expire_all()
    reports = {r.purchase.paddle_transaction_id: r for r in await purchases_of(db, user_id)}
    verdicts = {txn: (r.refundable, r.why) for txn, r in reports.items()}
    assert verdicts == {
        "txn_d": (False, "more than 14 days ago"),
        "txn_a": (False, "credit was spent after it"),
        "txn_b": (False, "credit was spent after it"),
        "txn_c": (True, "untouched and inside 14 days"),
        "txn_e": (False, "already refunded or charged back"),
    }
    assert reports["txn_a"].spent_since == Decimal("0.3")
    assert reports["txn_c"].spent_since == 0


async def test_listing_purchases_costs_one_statement_whatever_the_count(
    client: AsyncClient, db: AsyncSession, selling: None
) -> None:
    user = await _buyer(db)
    for i in range(6):
        await _deliver(client, _transaction(f"txn_{i}"))
    user_id = user.id
    db.expire_all()
    statements: list[str] = []

    def count(*args: Any) -> None:
        statements.append(str(args[2]))

    engine = db.bind.sync_engine  # type: ignore[union-attr]
    sa.event.listen(engine, "before_cursor_execute", count)
    try:
        reports = await purchases_of(db, user_id)
    finally:
        sa.event.remove(engine, "before_cursor_execute", count)
    assert len(reports) == 6
    assert len(statements) == 1, statements

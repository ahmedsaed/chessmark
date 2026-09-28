"""Buying credit through Paddle, from the checkout to the buyer's balance (ADR-0055, ADR-0056).

Driven through the HTTP endpoints, with Paddle's API and OpenRouter's balance swapped for fakes and
webhook deliveries signed the way Paddle signs them, because the rules that matter live across the
boundary:

* a checkout reserves its credit, and credit is sold only while OpenRouter's balance covers it —
  two buyers at once cannot both take the last of it;
* a purchase credits what its reservation says, once, however often Paddle delivers it;
* a delivery we cannot verify, or one that does not match its reservation, moves nothing;
* a refund takes back what the purchase granted and no more — even below zero.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.api.deps import get_openrouter_balance, get_paddle_api, get_sales
from chessmark.core.config import Settings, get_settings
from chessmark.core.paddle_api import PaddleApiError
from chessmark.core.paddle_signature import sign
from chessmark.db.enums import CreditReason, PurchaseStatus, ReservationStatus
from chessmark.db.models import CreditLedger, CreditReservation, Purchase, User
from chessmark.db.purchases import purchases_of
from tests.api.conftest import as_user

pytestmark = pytest.mark.integration

SECRET = "pdl_ntfset_test"
BUYER = "user_buyer"


@dataclass
class FakeBalance:
    """OpenRouter's remaining credit, as the test sets it. `None` is OpenRouter not answering.

    `value` is what a fresh read returns; `stored` is what the worker last stored, which the page
    reads. Kept apart so a test can tell which one a path used."""

    value: Decimal | None = Decimal(1000)
    stored_value: Decimal | None = Decimal(1000)
    fresh_reads: int = 0

    async def fresh(self) -> Decimal | None:
        self.fresh_reads += 1
        return self.value

    async def stored(self) -> Decimal | None:
        return self.stored_value


@dataclass
class FakePaddle:
    """Paddle's transaction API: records what it was asked for, hands out ids, or refuses."""

    refuse: bool = False
    made: list[dict[str, Any]] = field(default_factory=list)

    async def create_transaction(self, **kwargs: Any) -> str:
        if self.refuse:
            raise PaddleApiError("refused")
        self.made.append(kwargs)
        return f"txn_{len(self.made):026d}"


@dataclass
class FakeSales:
    """The operator's switch (ADR-0057). Open in these tests unless one pauses it."""

    open_: bool = True

    async def is_open(self) -> bool:
        return self.open_


@dataclass
class Selling:
    balance: FakeBalance
    paddle: FakePaddle
    sales: FakeSales


@pytest.fixture
def selling(app: FastAPI) -> Selling:
    settings = Settings(
        paddle_webhook_secret=SECRET,
        paddle_api_key="pdl_sdbx_apikey_test",
        paddle_product_id="pro_test",
        openrouter_management_key="mgmt",
        credit_reserve_usd=10.0,
        paddle_tax_preview_price_id="pri_preview",
    )
    fakes = Selling(balance=FakeBalance(), paddle=FakePaddle(), sales=FakeSales())
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_openrouter_balance] = lambda: fakes.balance
    app.dependency_overrides[get_paddle_api] = lambda: fakes.paddle
    app.dependency_overrides[get_sales] = lambda: fakes.sales
    return fakes


async def _buyer(db: AsyncSession, clerk_id: str = BUYER, *, usd: str = "0") -> User:
    user = User(
        clerk_user_id=clerk_id, email=f"{clerk_id}@chessmark.test", balance_usd=Decimal(usd)
    )
    db.add(user)
    await db.commit()
    return user


async def _checkout(client: AsyncClient, amount: int | str, clerk_id: str = BUYER) -> Response:
    return await client.post(
        "/credit/checkout", json={"amount_usd": str(amount)}, headers=as_user(clerk_id)
    )


def _completed(
    txn: str, reservation_id: str | None, *, total: str, tax: str = "0", currency: str = "USD"
) -> dict[str, Any]:
    """Paddle's `transaction.completed`. Prices include tax, so `total` is the amount the buyer
    chose and `tax` is the part of it that went to tax."""
    return {
        "event_type": "transaction.completed",
        "data": {
            "id": txn,
            "status": "completed",
            "customer_id": "ctm_1",
            "currency_code": currency,
            "custom_data": {"reservation_id": reservation_id} if reservation_id else None,
            "items": [{"price": {"id": "pri_nonCatalog"}, "quantity": 1}],
            "details": {
                "totals": {
                    "currency_code": currency,
                    "subtotal": str(int(total) - int(tax)),
                    "tax": tax,
                    "total": total,
                    "grand_total": total,
                    "fee": "100",
                    "earnings": "900",
                }
            },
        },
    }


def _reservation_of(selling: Selling, index: int = -1) -> str:
    reservation: str = selling.paddle.made[index]["custom_data"]["reservation_id"]
    return reservation


async def _buy(
    client: AsyncClient, selling: Selling, amount: int, clerk_id: str = BUYER
) -> tuple[str, dict[str, Any]]:
    """Check out and complete the payment: the transaction id and its completion event."""
    response = await _checkout(client, amount, clerk_id)
    assert response.status_code == 200, response.text
    txn = response.json()["transaction_id"]
    event = _completed(txn, _reservation_of(selling), total=str(amount * 100))
    assert (await _deliver(client, event)).status_code == 200
    return txn, event


def _adjustment(
    adj: str, action: str, txn: str, *, total: str, status: str = "approved"
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


async def _expire_reservations(db: AsyncSession) -> None:
    await db.execute(
        sa.update(CreditReservation).values(
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        )
    )
    await db.commit()


# ------------------------------------------------------------------------- what an amount is


async def test_the_options_are_public_and_lay_out_each_preset_as_a_sum(
    client: AsyncClient, selling: Selling
) -> None:
    body = (await client.get("/credit/options")).json()
    assert (body["selling"], body["min_usd"], body["max_usd"]) == (True, "5", "100")
    assert body["tax_preview_price_id"] == "pri_preview"
    rows = [
        (
            p["price_usd"],
            p["processor_fee_usd"],
            p["upkeep_usd"],
            p["provider_fee_usd"],
            p["credit_usd"],
        )
        for p in body["presets"]
    ]
    assert rows == [
        ("5", "0.75", "0.25", "0.21", "3.79"),
        ("10", "1.00", "0.50", "0.45", "8.05"),
        ("25", "1.75", "1.25", "1.15", "20.85"),
    ]


async def test_nothing_is_on_sale_until_paddle_and_openrouter_are_configured(
    app: FastAPI, client: AsyncClient, db: AsyncSession
) -> None:
    await _buyer(db)
    app.dependency_overrides[get_settings] = lambda: Settings(
        paddle_webhook_secret="", paddle_api_key="", paddle_product_id=""
    )
    options = (await client.get("/credit/options")).json()
    assert options["selling"] is False
    assert options["tax_preview_price_id"] is None, "no estimate for a price nobody can buy"
    assert (await _checkout(client, 5)).status_code == 503


async def test_any_whole_amount_in_range_is_quoted_and_anything_else_says_why(
    client: AsyncClient,
) -> None:
    quoted = await client.get("/credit/quote", params={"amount": "37"})
    assert quoted.json()["credit_usd"] == "31.09"
    refused = await client.get("/credit/quote", params={"amount": "4"})
    assert refused.status_code == 422
    assert "between $5 and $100" in refused.json()["detail"]


# ------------------------------------------------------------------------- the checkout


async def test_a_checkout_reserves_its_credit_and_creates_the_transaction_at_its_price(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    response = await _checkout(client, 37)
    assert response.status_code == 200, response.text
    made = selling.paddle.made[0]
    assert (made["price_usd"], made["credit_usd"], made["product_id"]) == (
        Decimal(37),
        Decimal("31.09"),
        "pro_test",
    )
    reservation = await db.scalar(sa.select(CreditReservation))
    assert reservation is not None
    assert reservation.status is ReservationStatus.OPEN
    assert reservation.credit_usd == Decimal("31.09")
    assert reservation.paddle_transaction_id == response.json()["transaction_id"]
    assert made["custom_data"]["reservation_id"] == str(reservation.id)


async def test_credit_is_not_sold_beyond_what_openrouter_can_cover(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """$30 left at OpenRouter, $10 kept back: $20 of credit can be sold, which $24 buys."""
    await _buyer(db)
    selling.balance.value = Decimal(30)
    refused = await _checkout(client, 25)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Only up to $24 can be bought right now."
    assert (await _checkout(client, 24)).status_code == 200


async def test_credit_users_already_hold_counts_against_the_headroom(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    await _buyer(db, "user_rich", usd="18")
    selling.balance.value = Decimal(30)  # $20 sellable, $18 of it already held by someone
    refused = await _checkout(client, 5)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Credit is sold out for now. Please try again later."


async def test_two_buyers_at_once_cannot_both_take_the_last_of_it(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """Room for one $5 purchase ($3.79 of $5 sellable). Both ask at the same moment.

    End to end only: these requests do not truly overlap, so this passes without the lock too.
    What proves the lock is `tests/db/test_capacity.py`, with two sessions held open at once."""
    await _buyer(db)
    await _buyer(db, "user_other")
    selling.balance.value = Decimal(15)
    responses = await asyncio.gather(_checkout(client, 5), _checkout(client, 5, "user_other"))
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_an_abandoned_checkout_gives_its_share_back_when_it_expires(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    await _buyer(db, "user_other")
    selling.balance.value = Decimal(15)
    assert (await _checkout(client, 5)).status_code == 200
    assert (await _checkout(client, 5, "user_other")).status_code == 409
    await _expire_reservations(db)
    assert (await _checkout(client, 5, "user_other")).status_code == 200


async def test_an_unknown_openrouter_balance_sells_nothing(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    selling.balance.value = None
    assert (await _checkout(client, 5)).status_code == 503
    assert await db.scalar(sa.select(sa.func.count()).select_from(CreditReservation)) == 0


async def test_a_checkout_paddle_refuses_releases_its_reservation(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    selling.balance.value = Decimal(15)
    selling.paddle.refuse = True
    assert (await _checkout(client, 5)).status_code == 502
    db.expire_all()
    statuses = set(await db.scalars(sa.select(CreditReservation.status)))
    assert statuses == {ReservationStatus.RELEASED}
    selling.paddle.refuse = False
    assert (await _checkout(client, 5)).status_code == 200, "the refused hold was given back"


# ------------------------------------------------------------------------- the webhook


async def test_a_completed_purchase_credits_what_was_reserved_once(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    _, event = await _buy(client, selling, 10)
    for _ in range(2):  # Paddle delivers at least once, and retries what it did not see acked
        assert (await _deliver(client, event)).status_code == 200

    assert await _balance(db) == Decimal("8.05")
    ledger = list(await db.scalars(sa.select(CreditLedger)))
    assert [(row.reason, row.delta) for row in ledger] == [(CreditReason.PURCHASE, Decimal("8.05"))]
    purchase = await db.scalar(sa.select(Purchase))
    assert purchase is not None and ledger[0].purchase_id == purchase.id
    assert purchase.status is PurchaseStatus.CREDITED
    reservation = await db.scalar(sa.select(CreditReservation))
    assert reservation is not None and reservation.status is ReservationStatus.CONSUMED


async def test_concurrent_deliveries_of_one_purchase_credit_it_once(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """The unique transaction id, not a read-then-write, is what stops the second credit."""
    await _buyer(db)
    txn = (await _checkout(client, 10)).json()["transaction_id"]
    event = _completed(txn, _reservation_of(selling), total="1000")
    responses = await asyncio.gather(*(_deliver(client, event) for _ in range(5)))
    assert {r.status_code for r in responses} == {200}
    assert await _balance(db) == Decimal("8.05")


async def test_consumed_credit_moves_from_reserved_to_held_and_is_counted_once(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """After paying, the buyer holds the credit and the reservation no longer does: the headroom
    is what it was with the reservation open — not doubled, and not freed."""
    await _buyer(db)
    await _buyer(db, "user_other")
    selling.balance.value = Decimal("23.79")  # $13.79 sellable: $3.79 bought, $10.00 of room left
    await _buy(client, selling, 5)
    assert (await _checkout(client, 13, "user_other")).status_code == 409  # $10.44 of credit
    assert (await _checkout(client, 12, "user_other")).status_code == 200  # $9.59 of credit


async def test_a_delivery_that_is_not_paddles_moves_nothing(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    txn = (await _checkout(client, 10)).json()["transaction_id"]
    event = _completed(txn, _reservation_of(selling), total="1000")
    assert (await _deliver(client, event, secret="pdl_ntfset_forged")).status_code == 401
    assert await db.scalar(sa.select(sa.func.count()).select_from(Purchase)) == 0
    assert await _balance(db) == 0


async def test_a_payment_that_does_not_match_its_reservation_is_recorded_and_not_credited(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    txn = (await _checkout(client, 10)).json()["transaction_id"]

    await _deliver(client, _completed(txn, _reservation_of(selling), total="500"))
    await _deliver(client, _completed("txn_nobody", None, total="500"))

    purchases = {p.paddle_transaction_id: p for p in await db.scalars(sa.select(Purchase))}
    assert {p.status for p in purchases.values()} == {PurchaseStatus.UNMATCHED}
    assert "paid 500 cents, reserved 1000" in (purchases[txn].problem or "")
    assert "no reservation" in (purchases["txn_nobody"].problem or "")
    assert await _balance(db) == 0


async def test_a_payment_after_its_reservation_expired_is_still_credited(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """The buyer was slow at the card form. The money has moved; refusing it would be worse."""
    await _buyer(db)
    txn = (await _checkout(client, 5)).json()["transaction_id"]
    await _expire_reservations(db)
    await _deliver(client, _completed(txn, _reservation_of(selling), total="500"))
    assert await _balance(db) == Decimal("3.79")


# ------------------------------------------------------------------------- refunds


async def test_a_refund_takes_the_credit_back_once_it_is_approved(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    txn, _ = await _buy(client, selling, 10)

    pending = _adjustment("adj_1", "refund", txn, total="1000", status="pending_approval")
    await _deliver(client, pending)
    assert await _balance(db) == Decimal("8.05"), "a refund awaiting review has not happened"

    for _ in range(2):
        await _deliver(client, _adjustment("adj_1", "refund", txn, total="1000"))
    assert await _balance(db) == 0
    reasons = list(await db.scalars(sa.select(CreditLedger.reason).order_by(CreditLedger.id)))
    assert reasons == [CreditReason.PURCHASE, CreditReason.PURCHASE_REFUNDED]


async def test_a_partial_refund_takes_back_its_share(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    txn, _ = await _buy(client, selling, 10)
    await _deliver(client, _adjustment("adj_half", "refund", txn, total="500"))
    assert await _balance(db) == Decimal("4.025")


async def test_a_chargeback_can_leave_a_spent_balance_below_zero_and_its_reversal_restores_it(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """The refund policy says so: the credit was spent, and the money went back anyway."""
    user = await _buyer(db)
    txn, _ = await _buy(client, selling, 10)
    await db.execute(sa.update(User).where(User.id == user.id).values(balance_usd=Decimal("1")))
    await db.commit()

    await _deliver(client, _adjustment("adj_cb", "chargeback", txn, total="1000"))
    assert await _balance(db) == Decimal("-7.05")

    await _deliver(client, _adjustment("adj_cbr", "chargeback_reverse", txn, total="1000"))
    assert await _balance(db) == Decimal("1")


async def test_refunds_and_chargebacks_never_take_back_more_than_was_granted(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    txn, _ = await _buy(client, selling, 10)
    await _deliver(client, _adjustment("adj_r", "refund", txn, total="1000"))
    await _deliver(client, _adjustment("adj_cb", "chargeback", txn, total="1000"))
    assert await _balance(db) == 0


# ------------------------------------------------------------------------- reading purchases


async def test_a_buyer_can_see_their_own_purchase_and_nobody_elses(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    await _buyer(db, "user_other")
    mine = as_user(BUYER)
    txn = (await _checkout(client, 10)).json()["transaction_id"]
    assert (await client.get(f"/credit/purchases/{txn}", headers=mine)).status_code == 404

    await _deliver(client, _completed(txn, _reservation_of(selling), total="1000"))
    body = (await client.get(f"/credit/purchases/{txn}", headers=mine)).json()
    assert body == {"status": "credited", "credit_usd": "8.05000000", "balance_usd": "8.05000000"}

    other = as_user("user_other")
    assert (await client.get(f"/credit/purchases/{txn}", headers=other)).status_code == 404


async def _at(db: AsyncSession, txn: str, when: dt.datetime) -> None:
    await db.execute(
        sa.update(Purchase).where(Purchase.paddle_transaction_id == txn).values(created_at=when)
    )
    await db.commit()


async def _spend_at(db: AsyncSession, user_id: Any, usd: str, when: dt.datetime) -> None:
    db.add(
        CreditLedger(
            user_id=user_id,
            delta=-Decimal(usd),
            balance_after=Decimal(0),
            reason=CreditReason.TURN,
            created_at=when,
        )
    )
    await db.commit()


async def test_a_purchase_is_refundable_only_while_untouched_and_inside_fourteen_days(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """The refund policy on a pooled balance: of two purchases made before a game, neither is
    untouched; one made after the game still is, until its fourteen days run out."""
    user = await _buyer(db)
    user_id = user.id
    now = dt.datetime.now(dt.UTC)
    day = dt.timedelta(days=1)
    txns = {}
    for name, amount, age in (("a", 5, 5), ("b", 10, 4), ("c", 25, 2), ("d", 5, 20), ("e", 5, 1)):
        txns[name], _ = await _buy(client, selling, amount)
        await _at(db, txns[name], now - age * day)
    await _spend_at(db, user_id, "0.30", now - 3 * day)
    await _deliver(client, _adjustment("adj_e", "refund", txns["e"], total="500"))

    db.expire_all()
    reports = {r.purchase.paddle_transaction_id: r for r in await purchases_of(db, user_id)}
    verdicts = {name: (reports[txn].refundable, reports[txn].why) for name, txn in txns.items()}
    assert verdicts == {
        "d": (False, "more than 14 days ago"),
        "a": (False, "credit was spent after it"),
        "b": (False, "credit was spent after it"),
        "c": (True, "untouched and inside 14 days"),
        "e": (False, "already refunded or charged back"),
    }
    assert reports[txns["a"]].spent_since == Decimal("0.3")
    assert reports[txns["c"]].spent_since == 0


async def test_listing_purchases_costs_one_statement_whatever_the_count(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    user = await _buyer(db)
    for _ in range(5):  # the checkout rate limit's worth in one minute
        await _buy(client, selling, 5)
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
    assert len(reports) == 5
    assert len(statements) == 1, statements


# ------------------------------------------------------------------------- before Buy is pressed


async def test_the_page_learns_what_can_be_bought_without_asking_openrouter(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    selling.balance.stored_value = Decimal(30)  # $20 sellable: up to $24
    assert (await client.get("/credit/availability")).json() == {
        "state": "available",
        "largest_usd": "24",
    }
    selling.balance.stored_value = Decimal(12)
    assert (await client.get("/credit/availability")).json()["state"] == "sold_out"
    selling.balance.stored_value = None  # never stored, or too old to stand for now
    assert (await client.get("/credit/availability")).json()["state"] == "unknown"
    assert selling.balance.fresh_reads == 0, "a page view must never call OpenRouter"


async def test_availability_is_off_until_selling_is_configured(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        paddle_webhook_secret="", paddle_api_key="", paddle_product_id=""
    )
    assert (await client.get("/credit/availability")).json() == {
        "state": "off",
        "largest_usd": None,
    }


async def test_buying_reads_openrouter_fresh_not_the_stored_value(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """The stored value says plenty; OpenRouter, asked now, says otherwise. Buying believes the
    fresh read, because it is about to promise credit."""
    await _buyer(db)
    selling.balance.stored_value = Decimal(1000)
    selling.balance.value = Decimal(12)
    assert (await _checkout(client, 5)).status_code == 409
    assert selling.balance.fresh_reads == 1


async def test_a_buyer_holds_one_open_checkout_at_a_time(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """Room for one $5 purchase. Pressing Buy again replaces the earlier checkout rather than
    holding a second share of the headroom — or one person could sell the site out unpaid."""
    await _buyer(db)
    await _buyer(db, "user_other")
    selling.balance.value = Decimal(15)
    assert (await _checkout(client, 5)).status_code == 200
    assert (await _checkout(client, 5)).status_code == 200, "the buyer's own hold was replaced"
    db.expire_all()
    statuses = sorted(await db.scalars(sa.select(CreditReservation.status)))
    assert statuses == sorted([ReservationStatus.RELEASED, ReservationStatus.OPEN])
    assert (await _checkout(client, 5, "user_other")).status_code == 409


async def test_checkouts_are_rate_limited_per_person(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    codes = [(await _checkout(client, 5)).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert selling.balance.fresh_reads == 5, "a refused request must not reach OpenRouter"


async def test_the_credit_is_settled_from_the_tax_paddle_actually_charged(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """$37 paid from Egypt, with $4.54 of VAT inside it. The checkout could only reserve the
    no-tax maximum ($31.09); the webhook grants what the amount pays for once tax is out of it."""
    await _buyer(db)
    txn = (await _checkout(client, 37)).json()["transaction_id"]
    reserved = await db.scalar(sa.select(CreditReservation.credit_usd))
    assert reserved == Decimal("31.09")
    await _deliver(client, _completed(txn, _reservation_of(selling), total="3700", tax="454"))
    assert await _balance(db) == Decimal("26.99")
    purchase = await db.scalar(sa.select(Purchase))
    assert purchase is not None and purchase.credit_usd == Decimal("26.99")


async def test_the_quote_takes_the_tax_out_of_the_amount(client: AsyncClient) -> None:
    for params in ({"amount": "37", "tax": "4.54"}, {"amount": "37", "tax_cents": "454"}):
        body = (await client.get("/credit/quote", params=params)).json()
        assert (body["price_usd"], body["tax_usd"], body["credit_usd"]) == ("37", "4.54", "26.99")
    refused = await client.get("/credit/quote", params={"amount": "37", "tax": "40"})
    assert refused.status_code == 422


async def test_a_payment_in_another_currency_is_not_credited_as_dollars(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """3700 of anything is not $37. The transaction is created in dollars, so a completion in
    another currency is something we did not sell, and an operator settles it."""
    await _buyer(db)
    txn = (await _checkout(client, 37)).json()["transaction_id"]
    event = _completed(txn, _reservation_of(selling), total="3700", currency="EUR")
    await _deliver(client, event)
    purchase = await db.scalar(sa.select(Purchase))
    assert purchase is not None and purchase.status is PurchaseStatus.UNMATCHED
    assert "EUR" in (purchase.problem or "")
    assert await _balance(db) == 0


async def test_a_tax_that_leaves_less_than_nothing_never_debits_the_buyer(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    await _buyer(db)
    txn = (await _checkout(client, 5)).json()["transaction_id"]
    await _deliver(client, _completed(txn, _reservation_of(selling), total="500", tax="490"))
    purchase = await db.scalar(sa.select(Purchase))
    assert purchase is not None and purchase.status is PurchaseStatus.UNMATCHED
    assert await _balance(db) == 0


@pytest.mark.parametrize("params", [{"tax": "NaN"}, {"tax": "sNaN"}, {"tax_cents": "x"}])
async def test_a_quote_with_tax_that_is_not_a_number_says_so(
    client: AsyncClient, params: dict[str, str]
) -> None:
    response = await client.get("/credit/quote", params={"amount": "37", **params})
    assert response.status_code == 422


# ------------------------------------------------------------------------------ the sales switch


async def test_a_paused_sale_refuses_new_checkouts_before_asking_anybody(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """Paused, a checkout is refused before OpenRouter is asked or anything is reserved — from a
    tab opened before the pause, too, which is why the API checks and not only the page."""
    await _buyer(db)
    selling.sales.open_ = False
    response = await _checkout(client, 5)
    assert response.status_code == 503
    assert "paused" in response.json()["detail"]
    assert selling.balance.fresh_reads == 0
    assert selling.paddle.made == []
    assert await db.scalar(sa.select(sa.func.count()).select_from(CreditReservation)) == 0


async def test_a_paused_sale_still_shows_what_an_amount_comes_to(
    client: AsyncClient, selling: Selling
) -> None:
    """The page says "paused" where Buy would be, and keeps the breakdown and the tax estimate."""
    selling.sales.open_ = False
    assert (await client.get("/credit/availability")).json() == {
        "state": "paused",
        "largest_usd": None,
    }
    assert (await client.get("/credit/options")).json()["tax_preview_price_id"] == "pri_preview"


async def test_a_purchase_paid_before_the_pause_is_still_credited(
    client: AsyncClient, db: AsyncSession, selling: Selling
) -> None:
    """The buyer was halfway through the checkout when sales paused. They paid; it is credited."""
    await _buyer(db)
    txn = (await _checkout(client, 10)).json()["transaction_id"]
    selling.sales.open_ = False
    await _deliver(client, _completed(txn, _reservation_of(selling), total="1000"))
    assert await _balance(db) == Decimal("8.05")

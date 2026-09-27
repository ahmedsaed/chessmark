"""What each credit pack grants, and reading which Paddle price is which (ADR-0055)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from chessmark.core.credit_packs import (
    PACK_PRICES,
    PackConfigError,
    credit_for,
    parse_packs,
    processor_fee,
    upkeep,
)


@pytest.mark.parametrize(("price", "credit"), [("5", "4.00"), ("10", "8.50"), ("25", "22.00")])
def test_a_pack_grants_its_price_less_paddles_fee_and_five_percent(price: str, credit: str) -> None:
    """The owner's rule: Paddle's 5% + $0.50 and 5% for infrastructure, nothing absorbed."""
    assert credit_for(Decimal(price)) == Decimal(credit)


def test_credit_rounds_down_so_it_never_grants_more_than_the_rule() -> None:
    assert credit_for(Decimal("7.77")) == Decimal("6.49")  # 6.493 by the rule


def test_the_price_ids_are_read_cheapest_first() -> None:
    packs = parse_packs("25=pri_c, 5=pri_a,10=pri_b")
    assert [(p.price_usd, p.price_id, p.credit_usd) for p in packs] == [
        (Decimal(5), "pri_a", Decimal("4.00")),
        (Decimal(10), "pri_b", Decimal("8.50")),
        (Decimal(25), "pri_c", Decimal("22.00")),
    ]


def test_no_price_ids_means_nothing_is_on_sale() -> None:
    assert parse_packs("") == ()


@pytest.mark.parametrize(
    "spec",
    ["7=pri_x", "5=prod_x", "5", "five=pri_x", "5=pri_x,10=pri_x"],
    ids=["no such pack", "not a price id", "no id", "not a number", "one id twice"],
)
def test_a_malformed_mapping_is_refused_rather_than_skipped(spec: str) -> None:
    with pytest.raises(PackConfigError):
        parse_packs(spec)


@pytest.mark.parametrize("price", [*PACK_PRICES, Decimal("7.77"), Decimal("3.33")])
def test_the_breakdown_the_page_shows_always_adds_up_to_the_price(price: Decimal) -> None:
    """The page lists price, fee, upkeep and credit as a sum; it must be one."""
    assert processor_fee(price) + upkeep(price) + credit_for(price) == price

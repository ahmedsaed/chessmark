"""What a purchase of credit costs and grants (ADR-0055, ADR-0056)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from chessmark.core.credit_pricing import AmountError, checked_amount, quote


@pytest.mark.parametrize(
    ("price", "processor", "upkeep", "provider", "credit"),
    [
        ("5", "0.75", "0.25", "0.21", "3.79"),
        ("10", "1.00", "0.50", "0.45", "8.05"),
        ("25", "1.75", "1.25", "1.15", "20.85"),
        ("100", "5.50", "5.00", "4.67", "84.83"),
    ],
)
def test_a_purchase_is_its_price_less_three_fees(
    price: str, processor: str, upkeep: str, provider: str, credit: str
) -> None:
    """The owner's rule, nothing absorbed: Paddle's 5% + $0.50, 5% for running the site, and
    OpenRouter's 5.5% on the credit we buy from it to pay for the usage."""
    q = quote(Decimal(price))
    assert (q.processor_fee_usd, q.upkeep_usd, q.provider_fee_usd, q.credit_usd) == tuple(
        Decimal(x) for x in (processor, upkeep, provider, credit)
    )


@pytest.mark.parametrize("price", range(5, 101))
def test_every_amount_adds_up_and_covers_its_provider_fee(price: int) -> None:
    """The page lays the lines out as a sum, so they must be one; and the credit plus OpenRouter's
    5.5% on it must never exceed what is left for it, or we would pay the difference."""
    q = quote(Decimal(price))
    assert q.processor_fee_usd + q.upkeep_usd + q.provider_fee_usd + q.credit_usd == q.price_usd
    assert q.credit_usd * Decimal("1.055") <= q.price_usd - q.processor_fee_usd - q.upkeep_usd


@pytest.mark.parametrize("raw", ["4", "101", "5.50", "abc", "-5"])
def test_an_amount_outside_the_range_or_not_whole_is_refused(raw: str) -> None:
    with pytest.raises(AmountError):
        checked_amount(raw)


@pytest.mark.parametrize("raw", ["5", "37", "100"])
def test_a_whole_amount_in_range_is_accepted(raw: str) -> None:
    assert checked_amount(raw) == Decimal(raw)

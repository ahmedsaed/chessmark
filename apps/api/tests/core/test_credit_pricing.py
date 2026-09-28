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
    assert q.tax_usd == 0
    assert (q.processor_fee_usd, q.upkeep_usd, q.provider_fee_usd, q.credit_usd) == tuple(
        Decimal(x) for x in (processor, upkeep, provider, credit)
    )


def test_tax_comes_out_of_the_amount_before_the_shares() -> None:
    """$37 paid from Egypt: 14% VAT inside it is $4.54. Paddle's fee is on the whole $37 ($2.35),
    running Chessmark is 5% of the $32.46 after tax ($1.63), and $28.48 is left to pay for the
    credit and OpenRouter's 5.5% on it: $26.99 of credit, $1.49 to OpenRouter."""
    q = quote(Decimal(37), Decimal("4.54"))
    assert (q.tax_usd, q.processor_fee_usd, q.upkeep_usd, q.provider_fee_usd, q.credit_usd) == (
        Decimal("4.54"),
        Decimal("2.35"),
        Decimal("1.63"),
        Decimal("1.49"),
        Decimal("26.99"),
    )


@pytest.mark.parametrize("tax", ["-0.01", "37", "40", "NaN", "sNaN"])
def test_a_tax_outside_the_amount_is_refused(tax: str) -> None:
    with pytest.raises(AmountError):
        quote(Decimal(37), Decimal(tax))


def test_a_tax_that_would_leave_less_than_nothing_is_refused() -> None:
    """$4.90 of tax on $5 leaves less than the processor's share. A negative credit would reach
    the webhook as a *debit* from the buyer, so it is refused rather than granted."""
    with pytest.raises(AmountError):
        quote(Decimal(5), Decimal("4.90"))


@pytest.mark.parametrize("price", range(5, 101))
def test_every_amount_adds_up_and_covers_its_provider_fee(price: int) -> None:
    """The page lays the lines out as a sum, so they must be one; and the credit plus OpenRouter's
    5.5% on it must never exceed what is left for it, or we would pay the difference."""
    for rate in (Decimal(0), Decimal("0.14"), Decimal("0.27")):
        tax = (Decimal(price) - Decimal(price) / (1 + rate)).quantize(Decimal("0.01"))
        q = quote(Decimal(price), tax)
        lines = q.tax_usd + q.processor_fee_usd + q.upkeep_usd + q.provider_fee_usd + q.credit_usd
        assert lines == q.price_usd
        left = q.price_usd - q.tax_usd - q.processor_fee_usd - q.upkeep_usd
        assert q.credit_usd * Decimal("1.055") <= left


@pytest.mark.parametrize("raw", ["4", "101", "5.50", "abc", "-5", "NaN", "sNaN", "Infinity"])
def test_an_amount_outside_the_range_or_not_whole_is_refused(raw: str) -> None:
    with pytest.raises(AmountError):
        checked_amount(raw)


@pytest.mark.parametrize("raw", ["5", "37", "100"])
def test_a_whole_amount_in_range_is_accepted(raw: str) -> None:
    assert checked_amount(raw) == Decimal(raw)


def test_an_amount_is_a_plain_number_of_dollars_however_it_was_written() -> None:
    """`1e1` is ten, but as `Decimal('1E+1')` it would reach Paddle's description as written."""
    assert str(checked_amount("1e1")) == "10"
    assert str(checked_amount("25.00")) == "25"

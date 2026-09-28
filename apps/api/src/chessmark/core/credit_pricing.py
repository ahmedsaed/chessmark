"""What a purchase of credit costs and grants (ADR-0055, ADR-0056).

**What a purchase grants is decided here, on the server, and nowhere else.** The buyer names an
amount; the API quotes it, reserves the credit, and creates the Paddle transaction at that price
itself. Paddle's signed webhook then reports what was paid, and the reservation — ours — says what
it grants. Nothing the browser sends can change the credit.

**The amount the buyer chooses is what they pay, tax included** (the owner's choice), and **no fee
is absorbed** (the owner's rule). So a purchase is a sum the page lays out line by line, every line
coming out of the amount:

* the **tax**, where the buyer's country charges one — Paddle's figure, which is why the credit is
  only exact once Paddle knows the country (an estimate on the page, exact in the checkout, final
  in the webhook);
* the **payment processor**'s share: Paddle's 5% + $0.50 — of the whole amount, tax included,
  which is what Paddle charges it on;
* **running Chessmark**: 5% of what is left after tax;
* the **AI provider**'s fee: OpenRouter charges 5.5% on the credit we buy from it to pay for the
  usage, so a dollar of Chessmark credit costs us $1.055 of OpenRouter's;
* and the **credit**: what is left.

The two shares round *up* to the cent, the credit is what then pays for itself plus the AI
provider's 5.5%, rounded *down*, and the AI provider's line is the remainder — so the lines always
add up to the amount, and rounding never grants more than the rule allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: A purchase is a whole number of dollars in this range. The floor keeps Paddle's fixed $0.50 from
#: eating most of a purchase; the ceiling caps what one refund or chargeback can cost, and how much
#: of the OpenRouter headroom one buyer can take.
MIN_USD = Decimal(5)
MAX_USD = Decimal(100)

#: Offered as one-click amounts beside the box that takes any other.
PRESETS = (Decimal(5), Decimal(10), Decimal(25))

PROCESSOR_RATE = Decimal("0.05")
PROCESSOR_FIXED = Decimal("0.50")
UPKEEP_RATE = Decimal("0.05")
#: OpenRouter's fee on buying its credits by card (5.5%, $0.80 minimum per top-up — the minimum is
#: per top-up of our own, amortised over many purchases, so it is not charged per purchase).
PROVIDER_RATE = Decimal("0.055")

CENT = Decimal("0.01")


class AmountError(ValueError):
    """Not a whole number of dollars between `MIN_USD` and `MAX_USD`."""


@dataclass(frozen=True, slots=True)
class Quote:
    #: What the buyer pays, tax included.
    price_usd: Decimal
    tax_usd: Decimal
    processor_fee_usd: Decimal
    upkeep_usd: Decimal
    provider_fee_usd: Decimal
    credit_usd: Decimal


def _up(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding="ROUND_UP")


def _down(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding="ROUND_DOWN")


def checked_amount(raw: Decimal | int | str) -> Decimal:
    """The amount, if it may be bought; `AmountError` saying why otherwise."""
    try:
        amount = Decimal(str(raw))
    except ArithmeticError as error:
        raise AmountError(f"{raw!r} is not an amount of dollars") from error
    # Before any comparison: comparing a signalling NaN raises rather than answering, which would
    # surface as a 500 instead of the reason.
    if not amount.is_finite():
        raise AmountError(f"{raw!r} is not an amount of dollars")
    if amount != amount.to_integral_value():
        raise AmountError("Choose a whole number of dollars.")
    if not MIN_USD <= amount <= MAX_USD:
        raise AmountError(f"Choose between ${MIN_USD} and ${MAX_USD}.")
    # `1e1` and `25.00` are fine amounts, but kept as written they reach Paddle's line item as
    # "$1E+1" — so the amount is a plain number of dollars from here on.
    return Decimal(int(amount))


def quote(price: Decimal, tax: Decimal = Decimal(0)) -> Quote:
    """The breakdown of a purchase at this price, with this much of it going to tax.

    Call `checked_amount` first for buyer input. Without a tax — before Paddle knows the buyer's
    country — this is also the most a purchase can grant, which is what a checkout reserves.
    """
    if not tax.is_finite() or not Decimal(0) <= tax < price:
        raise AmountError("The tax must be at least zero and less than the amount.")
    processor = _up(price * PROCESSOR_RATE + PROCESSOR_FIXED)
    upkeep = _up((price - tax) * UPKEEP_RATE)
    left = price - tax - processor - upkeep
    # A negative credit would reach the webhook as a debit from the person who just paid. No real
    # tax rate comes near this, so a tax that does is refused and the purchase settled by hand.
    if left < 0:
        raise AmountError("The tax leaves nothing of the amount to buy credit with.")
    credit = _down(left / (1 + PROVIDER_RATE))
    return Quote(
        price_usd=price,
        tax_usd=tax,
        processor_fee_usd=processor,
        upkeep_usd=upkeep,
        provider_fee_usd=left - credit,
        credit_usd=credit,
    )


__all__ = [
    "MAX_USD",
    "MIN_USD",
    "PRESETS",
    "AmountError",
    "Quote",
    "checked_amount",
    "quote",
]

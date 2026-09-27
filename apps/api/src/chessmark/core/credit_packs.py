"""The credit packs sold through Paddle, and what each one grants (ADR-0055).

**What a pack grants is decided here, on the server, and nowhere else.** The browser names a Paddle
price to open a checkout, and Paddle's signed webhook names the price that was paid for; this table
turns that price into credit. Nothing a buyer's browser sends can change the amount — not the
checkout's `customData`, not the price's own `custom_data` (which is a label for the dashboard).

**The owner's rule: no fee is absorbed.** Paddle keeps 5% + $0.50 of a sale and 5% more pays for
the infrastructure, so a pack grants `price * 0.90 - $0.50`: $5 → $4.00, $10 → $8.50, $25 → $22.00.
The amounts are shown to the buyer before they pay — the price names in Paddle's catalog say them
too — and are exact, never estimated from what a particular sale's fee turned out to be.

The *price ids* come from the environment, because Paddle's sandbox and live catalogues are
separate and the same pack has a different id in each.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: What each pack costs, in US dollars.
PACK_PRICES = (Decimal(5), Decimal(10), Decimal(25))

#: Paddle's share of a sale, plus the share kept for infrastructure.
FEE_RATE = Decimal("0.05")
FEE_FIXED = Decimal("0.50")
INFRA_RATE = Decimal("0.05")

CENT = Decimal("0.01")


def credit_for(price: Decimal) -> Decimal:
    """The credit a pack at this price grants, rounded down to the cent so it never grants more
    than the rule allows."""
    granted = price - (price * FEE_RATE + FEE_FIXED) - price * INFRA_RATE
    return granted.quantize(CENT, rounding="ROUND_DOWN")


@dataclass(frozen=True, slots=True)
class Pack:
    price_id: str
    price_usd: Decimal
    credit_usd: Decimal


class PackConfigError(ValueError):
    """`PADDLE_PRICE_IDS` names a pack that does not exist, or is not `<dollars>=<price id>`."""


def parse_packs(spec: str) -> tuple[Pack, ...]:
    """`"5=pri_…,10=pri_…,25=pri_…"` into packs, cheapest first. Empty means selling is off.

    A malformed entry raises rather than being skipped: a pack that silently vanished from the
    page is a sale lost, and one mapped to the wrong price is credit granted at the wrong rate.
    """
    packs: list[Pack] = []
    for entry in (part.strip() for part in spec.split(",")):
        if not entry:
            continue
        dollars, _, price_id = entry.partition("=")
        try:
            price = Decimal(dollars.strip())
        except ArithmeticError as error:
            raise PackConfigError(f"{entry!r} is not <dollars>=<price id>") from error
        price_id = price_id.strip()
        if price not in PACK_PRICES or not price_id.startswith("pri_"):
            raise PackConfigError(
                f"{entry!r}: packs are {', '.join(f'${p}' for p in PACK_PRICES)}, "
                "each mapped to a Paddle price id (pri_…)"
            )
        packs.append(Pack(price_id=price_id, price_usd=price, credit_usd=credit_for(price)))

    if len({pack.price_id for pack in packs}) != len(packs):
        raise PackConfigError("the same Paddle price id is mapped to two packs")
    return tuple(sorted(packs, key=lambda pack: pack.price_usd))


__all__ = ["PACK_PRICES", "Pack", "PackConfigError", "credit_for", "parse_packs"]

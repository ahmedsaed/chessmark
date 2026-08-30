"""What OpenRouter says our account has left (OPS-19).

One call, used for one purpose: deciding whether a credit halt can be lifted. A halt that only a
command lifts has an obvious failure — credits are topped up at 11pm and the pool sits idle until
somebody remembers — so something has to be able to check, and the balance is checkable.

**Deliberately not used to decide whether to spend.** That is what the daily kill switch and the
per-user quota are for (ADR-0011), both of which are ours and exact. This number comes from a third
party over a network and can be stale, wrong, or unavailable; the one thing it is good enough for is
answering "has somebody topped up since we stopped?".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

log = logging.getLogger(__name__)

CREDITS_URL = "https://openrouter.ai/api/v1/credits"

#: Short. Nothing waits on this — a probe that cannot answer leaves the halt exactly as it was,
#: which is the safe direction, and the next tick asks again.
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Balance:
    """Granted credits and what has been spent of them."""

    total: Decimal
    used: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.total - self.used

    @property
    def positive(self) -> bool:
        return self.remaining > 0


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse(payload: dict[str, Any]) -> Balance | None:
    """Read a balance out of the response, or `None` if it is not there.

    The endpoint answers `{"data": {"total_credits": N, "total_usage": M}}`. Both keys are read
    defensively and a missing or unparseable one yields `None` rather than a zero: **zero is a
    balance and `None` is ignorance**, and confusing the two here would either lift a halt that
    should stand or keep one that should not.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    total = _decimal(data.get("total_credits"))
    used = _decimal(data.get("total_usage"))
    if total is None or used is None:
        return None
    return Balance(total=total, used=used)


async def fetch_balance(api_key: str, *, client: httpx.AsyncClient | None = None) -> Balance | None:
    """Ask OpenRouter what is left. `None` when we could not find out.

    Never raises. Every failure — no key, a timeout, a 5xx, a body in a shape we did not expect —
    is the same answer as far as the caller is concerned: *we do not know*, so leave the halt
    alone. Letting this raise would put a network error on the reconciler's path, where it would
    stop the sweep that rescues stalled games.
    """
    if not api_key:
        return None

    owned = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        response = await http.get(
            CREDITS_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT_SECONDS
        )
        if response.status_code != httpx.codes.OK:
            log.info("credits probe answered %s", response.status_code)
            return None
        return parse(response.json())
    except Exception:
        log.info("credits probe did not answer", exc_info=True)
        return None
    finally:
        if owned:
            await http.aclose()

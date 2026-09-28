"""OpenRouter's remaining prepaid credit, as we last read it (ADR-0056).

Two readers, and they want different things:

* **The credit page** wants to say "sold out" or "up to $X" before anyone presses Buy, on every
  view. It reads the **stored** value, and never calls OpenRouter.
* **Buying** wants the truth, because it is about to promise credit. It reads OpenRouter **fresh**,
  and stores what it read.

The worker refreshes the stored value once a minute, beside its billing sweep. So OpenRouter is
asked a fixed number of times however many people look at the page: once a minute, plus once per
Buy — and purchases are rare.

**A stored value older than `STALE_AFTER` is treated as unknown**, never as current: it means the
worker has stopped refreshing it, and a figure from an hour ago could promise credit that has since
been spent. Unknown shows as "paused for a moment" and sells nothing.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Protocol

from chessmark.core.openrouter_billing import OpenRouterBilling

STORE_KEY = "chessmark:openrouter:balance"

#: Ten refreshes missed. Beyond this the stored balance says nothing about now.
STALE_AFTER = dt.timedelta(minutes=10)


class Balance(Protocol):
    async def stored(self) -> Decimal | None: ...

    async def fresh(self) -> Decimal | None: ...


class OpenRouterBalance:
    def __init__(self, redis: Any, billing: OpenRouterBilling) -> None:
        self._redis = redis
        self._billing = billing

    async def stored(self, *, now: dt.datetime | None = None) -> Decimal | None:
        """The last balance read, if it is recent enough to stand for now. No network call."""
        raw = await self._redis.get(STORE_KEY)
        if raw is None:
            return None
        try:
            record = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            read_at = dt.datetime.fromisoformat(record["at"])
            remaining = Decimal(record["remaining"])
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return None
        if (now or dt.datetime.now(dt.UTC)) - read_at > STALE_AFTER:
            return None
        return remaining

    async def fresh(self, *, now: dt.datetime | None = None) -> Decimal | None:
        """Ask OpenRouter, and store the answer. `None` if it would not say — which is stored as
        nothing, so the page does not keep showing the last good value for longer than it may."""
        remaining = await self._billing.remaining()
        if remaining is not None:
            at = (now or dt.datetime.now(dt.UTC)).isoformat()
            await self._redis.set(STORE_KEY, json.dumps({"remaining": str(remaining), "at": at}))
        return remaining


__all__ = ["STALE_AFTER", "STORE_KEY", "Balance", "OpenRouterBalance"]

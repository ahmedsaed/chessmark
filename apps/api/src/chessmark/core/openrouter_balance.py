"""OpenRouter's remaining prepaid credit, read at most once a minute (ADR-0056).

Every checkout needs it, and OpenRouter's `/credits` is an HTTP round trip that a burst of buyers
would otherwise make once each. A minute of staleness is safe: the balance only falls between
reads by what games spend, which the house reserve is there to absorb.

**An unknown balance is never cached**, and never read as zero or as plenty: the caller stops
selling until the next read answers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from chessmark.core.openrouter_billing import OpenRouterBilling

CACHE_KEY = "openrouter:remaining"
CACHE_SECONDS = 60


class Balance(Protocol):
    async def remaining(self) -> Decimal | None: ...


class CachedBalance:
    def __init__(self, redis: Any, billing: OpenRouterBilling) -> None:
        self._redis = redis
        self._billing = billing

    async def remaining(self) -> Decimal | None:
        cached = await self._redis.get(CACHE_KEY)
        if cached is not None:
            return Decimal(cached.decode() if isinstance(cached, bytes) else cached)
        remaining = await self._billing.remaining()
        if remaining is not None:
            await self._redis.set(CACHE_KEY, str(remaining), ex=CACHE_SECONDS)
        return remaining


__all__ = ["CACHE_KEY", "Balance", "CachedBalance"]

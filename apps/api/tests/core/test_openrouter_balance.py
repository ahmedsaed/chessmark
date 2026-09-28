"""The stored OpenRouter balance: fresh reads store it, and a stale one stands for nothing."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from chessmark.core.openrouter_balance import STALE_AFTER, OpenRouterBalance


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value


class FakeBilling:
    def __init__(self, remaining: Decimal | None) -> None:
        self.value = remaining

    async def remaining(self) -> Decimal | None:
        return self.value


NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)


def _balance(remaining: Decimal | None) -> tuple[OpenRouterBalance, FakeBilling]:
    billing = FakeBilling(remaining)
    balance = OpenRouterBalance(FakeRedis(), billing)  # type: ignore[arg-type]
    return balance, billing


async def test_a_fresh_read_is_what_the_page_then_reads() -> None:
    balance, _ = _balance(Decimal("42.5"))
    assert await balance.stored(now=NOW) is None
    assert await balance.fresh(now=NOW) == Decimal("42.5")
    assert await balance.stored(now=NOW + dt.timedelta(minutes=1)) == Decimal("42.5")


async def test_a_stored_balance_too_old_to_stand_for_now_is_unknown() -> None:
    """The worker stopped refreshing it. An hour-old figure could promise credit since spent."""
    balance, _ = _balance(Decimal("42.5"))
    await balance.fresh(now=NOW)
    assert await balance.stored(now=NOW + STALE_AFTER + dt.timedelta(seconds=1)) is None


async def test_openrouter_not_answering_leaves_the_last_good_value_to_age_out() -> None:
    balance, billing = _balance(Decimal("42.5"))
    await balance.fresh(now=NOW)
    billing.value = None
    assert await balance.fresh(now=NOW + dt.timedelta(minutes=1)) is None
    # Not overwritten with nothing, and not refreshed either: it goes stale on its own clock.
    assert await balance.stored(now=NOW + dt.timedelta(minutes=2)) == Decimal("42.5")
    assert await balance.stored(now=NOW + STALE_AFTER + dt.timedelta(minutes=1)) is None


@pytest.mark.parametrize(
    "raw", ["not json", '{"remaining": "x", "at": "2026-09-28T12:00:00+00:00"}']
)
async def test_a_garbled_stored_value_is_unknown(raw: Any) -> None:
    balance, _ = _balance(None)
    balance._redis.data["chessmark:openrouter:balance"] = raw  # type: ignore[attr-defined]
    assert await balance.stored(now=NOW) is None

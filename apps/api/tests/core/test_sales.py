"""The switch that opens and pauses credit sales (ADR-0057)."""

from __future__ import annotations

import datetime as dt

from chessmark.core.sales import KEY, Sales

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value


async def test_sales_are_closed_until_somebody_opens_them() -> None:
    """The first deploy with Paddle's keys must not start taking money on its own."""
    sales = Sales(FakeRedis())
    assert await sales.is_open() is False
    await sales.open(now=NOW)
    assert await sales.is_open() is True


async def test_a_pause_keeps_its_reason_and_when() -> None:
    sales = Sales(FakeRedis())
    await sales.open(now=NOW)
    await sales.pause("refunds need a look", now=NOW)
    state = await sales.state()
    assert (state.open, state.reason, state.at) == (False, "refunds need a look", NOW)


async def test_an_unreadable_switch_is_closed_not_open() -> None:
    """A bad write must stop sales, never start them."""
    redis = FakeRedis()
    sales = Sales(redis)
    for raw in ("not json", '{"open": "yes", "at": "2026-09-29T12:00:00+00:00"}', '{"open": true}'):
        redis.data[KEY] = raw
        assert await sales.is_open() is False, raw

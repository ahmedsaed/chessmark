"""Rate limiting.

The exit criterion is "100 rapid game-creation requests from one user are rate-limited, not
served", so that is the shape of the main test — concurrent, from one identity, counting how many
got through.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chessmark.core.ratelimit import RateLimiter

pytestmark = pytest.mark.integration


async def test_requests_under_the_limit_are_allowed(redis: Any) -> None:
    limiter = RateLimiter(redis, limit=5, window_seconds=60)

    for expected_remaining in (4, 3, 2, 1, 0):
        decision = await limiter.check("user-1")
        assert decision.allowed
        assert decision.remaining == expected_remaining


async def test_the_request_over_the_limit_is_refused(redis: Any) -> None:
    limiter = RateLimiter(redis, limit=3, window_seconds=60)
    for _ in range(3):
        assert await limiter.check("user-1")

    decision = await limiter.check("user-1")

    assert not decision.allowed
    assert decision.remaining == 0
    assert decision.retry_after >= 1, "a refusal must say when to come back"


async def test_a_hundred_rapid_requests_are_limited_not_served(redis: Any) -> None:
    """The Phase 9 exit criterion, run concurrently — which is how it would actually happen."""
    limiter = RateLimiter(redis, limit=10, window_seconds=60)

    decisions = await asyncio.gather(*(limiter.check("attacker") for _ in range(100)))
    allowed = sum(1 for decision in decisions if decision.allowed)

    assert allowed == 10, f"{allowed} of 100 got through, expected 10"


async def test_limits_are_per_identity(redis: Any) -> None:
    """One noisy user must not lock everyone else out."""
    limiter = RateLimiter(redis, limit=2, window_seconds=60)
    for _ in range(3):
        await limiter.check("loud")

    assert await limiter.check("quiet")


async def test_limits_are_per_action(redis: Any) -> None:
    """Exhausting the game-creation budget must not also block cheaper endpoints."""
    limiter = RateLimiter(redis, limit=1, window_seconds=60)
    await limiter.check("user-1", action="create_game")

    assert await limiter.check("user-1", action="something_else")


async def test_the_window_slides(redis: Any) -> None:
    """A fixed window would let the next window's full allowance through the instant the clock
    ticks over. Here the allowance returns gradually, as individual requests age out."""
    limiter = RateLimiter(redis, limit=2, window_seconds=1)
    for _ in range(2):
        await limiter.check("user-1")
    assert not await limiter.check("user-1")

    await asyncio.sleep(1.2)

    assert await limiter.check("user-1")


async def test_a_refused_attempt_still_counts(redis: Any) -> None:
    """Otherwise someone already over the limit can hammer the endpoint for free — every refusal
    would clear its own way for the next one."""
    limiter = RateLimiter(redis, limit=1, window_seconds=60)
    await limiter.check("user-1")

    for _ in range(5):
        assert not await limiter.check("user-1")


async def test_a_limit_of_zero_means_no_limiting(redis: Any) -> None:
    """Consistent with the global budget: an unset control does not silently become a total stop."""
    limiter = RateLimiter(redis, limit=0, window_seconds=60)

    for _ in range(50):
        assert await limiter.check("user-1")


async def test_the_key_expires(redis: Any) -> None:
    limiter = RateLimiter(redis, limit=5, window_seconds=30)
    await limiter.check("user-1", action="create_game")

    assert await redis.ttl("chessmark:rate:create_game:user-1") > 0


async def test_an_admin_can_clear_a_limit(redis: Any) -> None:
    limiter = RateLimiter(redis, limit=1, window_seconds=60)
    await limiter.check("user-1")
    assert not await limiter.check("user-1")

    await limiter.reset("user-1")

    assert await limiter.check("user-1")

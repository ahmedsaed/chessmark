"""Rate limiting for endpoints that can cost money (AUTH-06).

A **sliding window**, kept as a Redis sorted set of request timestamps, rather than the usual
fixed-window counter. A fixed window is one line shorter and lets through twice the limit at every
boundary: ten requests at 11:59:59 and ten more at 12:00:00 are twenty in one second, all legal.
For a cosmetic limit that is fine. This one guards a budget, so the boundary is worth closing.

Rate limiting is layered *in front of* the quota, not instead of it. The quota decides what a user
is allowed to spend in a day; this decides how fast they may ask. Both are needed: a quota alone
lets someone burn the whole allowance in one second, and a rate limit alone lets them spend it
steadily all day.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

KEY_PREFIX = "chessmark:rate"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    remaining: int
    limit: int
    #: Seconds until the oldest request leaves the window — what a `Retry-After` header needs.
    retry_after: int

    def __bool__(self) -> bool:
        return self.allowed


class RateLimiter:
    """A sliding-window limiter over Redis."""

    def __init__(self, redis: Any, *, limit: int, window_seconds: float) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds

    async def check(self, identity: str, *, action: str = "default") -> Decision:
        """Record an attempt and say whether it is allowed.

        The whole read-and-write is one pipeline, so a burst of concurrent requests cannot each see
        an empty window — the same reasoning as the quota reservation.
        """
        if self._limit <= 0:
            return Decision(allowed=True, remaining=0, limit=self._limit, retry_after=0)

        key = f"{KEY_PREFIX}:{action}:{identity}"
        now = time.time()
        cutoff = now - self._window

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        # A unique member per request: two requests in the same millisecond must both be counted,
        # and a sorted set keyed only by timestamp would silently collapse them into one.
        pipe.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.zcard(key)
        pipe.expire(key, int(self._window) + 1)
        _, _, count, _ = await pipe.execute()

        used = int(count)
        if used <= self._limit:
            return Decision(
                allowed=True,
                remaining=self._limit - used,
                limit=self._limit,
                retry_after=0,
            )

        # Over the line. The attempt stays in the window on purpose: a refused request still cost
        # us the work of refusing it, and dropping it would let someone hammer the endpoint for
        # free once they were already over.
        oldest = await self._redis.zrange(key, 0, 0, withscores=True)
        retry_after = 1
        if oldest:
            retry_after = max(1, int(self._window - (now - float(oldest[0][1]))) + 1)

        return Decision(allowed=False, remaining=0, limit=self._limit, retry_after=retry_after)

    async def reset(self, identity: str, *, action: str = "default") -> None:
        await self._redis.delete(f"{KEY_PREFIX}:{action}:{identity}")

"""The global daily spend kill switch — layer 1 of ADR-0011 (AUTH-05).

The outermost defence, and the only one that does not care who is spending or on what. It exists
because the other three layers all trust something: the per-user quota trusts that we identified
the user, the per-game cap trusts that the game is the unit of abuse, the per-turn ceiling trusts
that turns terminate. This layer trusts nothing except a counter.

**Counted in integer hundred-millionths of a dollar, never floats.** `llm_calls.cost_usd` is
`NUMERIC(16,8)`, so 1e-8 USD is the smallest amount the system can represent; counting in that unit
makes the Redis total exact rather than approximately right, which is what invariant 4 asks for.
Redis has no decimal type, and `INCRBYFLOAT` would accumulate error over thousands of calls a day.

The counter is keyed by **UTC date** and expires on its own. Nothing has to sweep it, and there is
no midnight job to fail to run — the day rolls over because the key name changes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

#: One unit of the counter, in USD. Matches the scale of `NUMERIC(16, 8)`.
UNIT = Decimal("0.00000001")

#: Long enough that a key survives any plausible clock skew, short enough that yesterday's
#: counters do not accumulate. Two days.
KEY_TTL_SECONDS = 172_800

KEY_PREFIX = "chessmark:spend"


def key_for(day: dt.date) -> str:
    return f"{KEY_PREFIX}:{day.isoformat()}"


def today() -> dt.date:
    """UTC, always.

    The quota resets at UTC midnight regardless of where the user or the server is, because a
    reset that follows a local timezone gives anyone who travels a second daily budget.
    """
    return dt.datetime.now(tz=dt.UTC).date()


def to_units(usd: Decimal) -> int:
    """USD to counter units, rounding **up**.

    Up, not nearest: a rounding rule that can round spend down lets a large number of tiny calls
    cost real money while registering as zero.
    """
    if usd <= 0:
        return 0
    return int((usd / UNIT).to_integral_value(rounding="ROUND_CEILING"))


def to_usd(units: int) -> Decimal:
    return Decimal(units) * UNIT


class GlobalBudget:
    """Today's total spend, and whether it has run out."""

    def __init__(self, redis: Any, *, daily_limit_usd: Decimal) -> None:
        self._redis = redis
        self._limit = daily_limit_usd

    @property
    def limit_usd(self) -> Decimal:
        return self._limit

    async def spent_today(self, *, day: dt.date | None = None) -> Decimal:
        raw = await self._redis.get(key_for(day or today()))
        return to_usd(int(raw)) if raw else Decimal(0)

    async def record(self, usd: Decimal, *, day: dt.date | None = None) -> Decimal:
        """Add to today's total and return the new total.

        The TTL is set on every call rather than only on creation. `EXPIRE` on an existing key is
        cheap, and the alternative — setting it only when the key is new — has a race in which two
        writers both see an existing key and neither sets a TTL, leaving it to live forever.
        """
        units = to_units(usd)
        key = key_for(day or today())

        pipe = self._redis.pipeline()
        pipe.incrby(key, units)
        pipe.expire(key, KEY_TTL_SECONDS)
        total, _ = await pipe.execute()

        return to_usd(int(total))

    async def tripped(self, *, day: dt.date | None = None) -> bool:
        """True when today's spend has reached the limit.

        A limit of zero or less means **no limit**, not "spend nothing" — an unset budget must not
        silently stop the whole system, which is the failure mode of treating a default of 0 as a
        cap. Refusing to run is a decision that should have to be made explicitly.
        """
        if self._limit <= 0:
            return False
        return await self.spent_today(day=day) >= self._limit

    async def remaining_usd(self, *, day: dt.date | None = None) -> Decimal | None:
        """What is left of today, or None when no limit is set."""
        if self._limit <= 0:
            return None
        return max(Decimal(0), self._limit - await self.spent_today(day=day))


# ---------------------------------------------------------------------- the free tier


#: OpenRouter allows 1,000 free-model requests a day once ten credits have been purchased (50
#: before that). It is **per account**, not per model or per event, so every pool, every human
#: game against a free model and every `make play` draw on the same allowance.
FREE_TIER_DAILY_REQUESTS = 1000

#: What we will actually use. The gap is not caution for its own sake:
#:
#: 1. A *failed* call leaves no `llm_calls` row — the row is written after a completion, and an
#:    error raises before it — so anything counted from the database undercounts by however many
#:    retries and 429s occurred. This counter is kept in Redis for that reason, but a process that
#:    dies mid-call still loses one.
#: 2. A pool running unattended must not consume the last of the day's allowance and leave a
#:    person unable to start a game.
FREE_TIER_RESERVE = 100

FREE_KEY_PREFIX = "chessmark:free-requests"


def free_key_for(day: dt.date) -> str:
    return f"{FREE_KEY_PREFIX}:{day.isoformat()}"


class FreeTierBudget:
    """Today's free-model request count, and whether the allowance has run out.

    Layer 1 of ADR-0011 counts dollars; this counts *requests*, because the free tier is not
    limited by money — it is limited by a number that no response header reports and no endpoint
    exposes. The only way to stay under it is to count our own calls and stop early.

    Exceeding it does not fail loudly. OpenRouter starts refusing with its own daily 429, which
    the gateway cannot tell apart from a provider's shared-pool 429, so it backs off politely and
    then abandons the game — meaning a pool would spend the rest of the day churning through
    pairings and producing nothing. Stopping first is the only way to avoid that.
    """

    def __init__(
        self,
        redis: Any,
        *,
        daily_limit: int = FREE_TIER_DAILY_REQUESTS,
        reserve: int = FREE_TIER_RESERVE,
    ) -> None:
        self._redis = redis
        self._limit = daily_limit
        self._reserve = reserve

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def usable(self) -> int:
        """The allowance minus the reserve — what an unattended event may actually spend."""
        return max(self._limit - self._reserve, 0)

    async def used_today(self, *, day: dt.date | None = None) -> int:
        raw = await self._redis.get(free_key_for(day or today()))
        return int(raw) if raw else 0

    async def record(self, requests: int = 1, *, day: dt.date | None = None) -> int:
        """Count calls we have made. Returns the new total.

        Called for **every attempt**, including ones that fail, because the provider counts those
        too and they are exactly what a database-derived count would miss.
        """
        key = free_key_for(day or today())
        pipe = self._redis.pipeline()
        pipe.incrby(key, requests)
        pipe.expire(key, KEY_TTL_SECONDS)
        total, _ = await pipe.execute()
        return int(total)

    async def remaining(self, *, day: dt.date | None = None) -> int:
        return max(self.usable - await self.used_today(day=day), 0)

    async def tripped(self, *, day: dt.date | None = None) -> bool:
        """True when the usable allowance is gone.

        A limit of zero or less means **no limit**, matching `GlobalBudget`: an unset budget must
        not silently stop the whole system.
        """
        if self._limit <= 0:
            return False
        return await self.used_today(day=day) >= self.usable

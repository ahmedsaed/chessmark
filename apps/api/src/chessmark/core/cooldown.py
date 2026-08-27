"""Endpoint cooldowns: what we remember after a provider says "not now".

A rate limit is a fact about an **endpoint**, not about the game that happened to discover it. A
free model is served by exactly one provider — every one of them, checked — so when that provider's
shared pool is hot, every game against that model will fail the same way. Keeping the knowledge in
the game that found it means the next game rediscovers it at full price, and the one after that.

That is not hypothetical. One free model went dark for ninety minutes; the pool paired it fourteen
times in a row, each pairing spending forty requests to learn the same thing, because nothing
between the games remembered. This is the memory between the games.

**Why the matchmaker is the important reader.** A cooldown that only makes a game wait more
politely still wastes the pairing. A cooldown the *matchmaker* consults means the game is never
started, so a pool with a concurrency of one spends that slot on a model that can actually play —
and the entrant comes back by itself when the key expires.

The ladder is ours, not the provider's, because the provider usually says nothing: OpenRouter
sends `Retry-After` only when every attempted provider returned a retry hint, and a single-endpoint
free model that returned none carries no hint at all. When it *does* say, it wins — it knows and we
are guessing.

Redis, keyed and expiring, for the same reason the spend counters are: nothing has to sweep it, and
a cooldown that outlived a worker restart by accident would be worse than one that is simply
forgotten.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

KEY_PREFIX = "chessmark:cooldown"

#: How long an endpoint is left alone after consecutive refusals. Minutes, not seconds: a shared
#: pool that just refused three requests in twenty seconds will refuse the fourth. The last value
#: repeats for every further strike.
LADDER_SECONDS = (60, 300, 900, 1800, 3600)

#: An unhinted cooldown never exceeds the last rung, so an endpoint that recovers quietly is
#: retried within the hour rather than written off for a day.
MAX_SECONDS = LADDER_SECONDS[-1]

#: Strikes are remembered for longer than the longest cooldown, so an endpoint that fails, waits
#: out an hour and fails again escalates instead of starting over. Not forever: a provider that was
#: hot last night and is fine today should not begin on the top rung.
STRIKE_TTL_SECONDS = 6 * 3600


def _slug(model: str, provider: str | None) -> str:
    """The key a cooldown is stored under.

    Keyed by model **and** provider. A model's seat is pinned to one endpoint for the whole game
    (ADR-0015), so in practice these are one thing — but a paid model with nineteen endpoints is
    not unavailable because one of them is, and a key on the model alone would say it was.
    """
    return f"{model}|{provider or '*'}"


class ProviderCooldown:
    """How long to leave an endpoint alone, and which models are currently resting."""

    def __init__(
        self,
        redis: Any,
        *,
        ladder: tuple[int, ...] = LADDER_SECONDS,
        strike_ttl: int = STRIKE_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._ladder = ladder
        self._strike_ttl = strike_ttl

    def _keys(self, model: str, provider: str | None) -> tuple[str, str]:
        slug = _slug(model, provider)
        return f"{KEY_PREFIX}:until:{slug}", f"{KEY_PREFIX}:strikes:{slug}"

    async def note(
        self,
        model: str,
        *,
        provider: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> int:
        """Record a refusal and return how many seconds this endpoint is now resting for.

        The provider's own hint wins when there is one; otherwise the strike count picks a rung.
        A hint is still floored at the first rung — a provider that says "retry in 1s" while
        refusing every request is describing its ideal, not our experience.
        """
        until_key, strike_key = self._keys(model, provider)

        pipe = self._redis.pipeline()
        pipe.incr(strike_key)
        pipe.expire(strike_key, self._strike_ttl)
        strikes, _ = await pipe.execute()

        rung = self._ladder[min(int(strikes), len(self._ladder)) - 1]
        seconds = int(min(max(retry_after_seconds or 0, rung), MAX_SECONDS))

        await self._redis.set(until_key, seconds, ex=seconds)
        return seconds

    async def clear(self, model: str, *, provider: str | None = None) -> None:
        """Forget an endpoint's history, after it serves a call successfully.

        Without this the ladder only ever climbs, and a model that was briefly hot last week would
        eventually rest for an hour over a single refusal.
        """
        until_key, strike_key = self._keys(model, provider)
        await self._redis.delete(until_key, strike_key)

    async def remaining(self, model: str, *, provider: str | None = None) -> int:
        """Seconds left, or 0 if this endpoint is not resting.

        Read from the key's own TTL rather than from a stored timestamp, so there is one source of
        truth about when it lifts and no way for the value and the expiry to disagree.
        """
        until_key, _ = self._keys(model, provider)
        ttl = await self._redis.ttl(until_key)
        return max(int(ttl), 0) if ttl is not None and int(ttl) > 0 else 0

    async def resting(self, models: list[str]) -> set[str]:
        """Which of these models has an endpoint resting right now.

        Takes the whole field in one pass, because the matchmaker asks about every entrant on every
        tick and a round trip each would make the tick cost grow with the pool.

        Matched on the model half of the key, so a slug pinned to a provider is found whether or
        not the caller knows which one — a tournament entrant is a slug, and the endpoint it
        pinned was chosen per game.
        """
        if not models:
            return set()

        pipe = self._redis.pipeline()
        for model in models:
            pipe.keys(f"{KEY_PREFIX}:until:{model}|*")
        found = await pipe.execute()
        return {model for model, hits in zip(models, found, strict=True) if hits}


def resume_at(seconds: int, *, now: dt.datetime | None = None) -> dt.datetime:
    """When a game paused for `seconds` may run again.

    Stored on the game as an absolute time rather than a duration: the resumer asks "is it time
    yet", and a duration would need to be added to whichever timestamp the reader happened to pick.
    """
    return (now or dt.datetime.now(dt.UTC)) + dt.timedelta(seconds=seconds)


__all__ = ["LADDER_SECONDS", "MAX_SECONDS", "ProviderCooldown", "resume_at"]

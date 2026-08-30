"""The daily free-model allowance is a 429 that means something else entirely (OPS-20).

OpenRouter caps free models at 1,000 requests a day **across the account** (50 before ten credits
are bought), and reports it as `429 "Rate limit exceeded: free-models-per-day"`. On the wire it is
indistinguishable from a provider's shared pool being hot; in meaning it is the opposite of one.

Classified as an ordinary rate limit it rested a single model for sixty seconds, the matchmaker
paired the next entrant, that refused identically because the cap is on the *account*, and a
seventeen-model pool worked through its whole field one doomed request at a time — the shape the
402 halt was built for, arriving through a different code.

It is also the easiest halt to lift: `X-RateLimit-Reset` says exactly when the cap goes, so the
halt is written with that as its TTL and Redis lifts it. No probe, no midnight job.

The per-minute cap is deliberately *not* covered here. That one is a short wait and the cooldown
ladder is right for it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from chessmark.agents import llm
from chessmark.agents.scripted import plays, scripted, step, tool_call
from chessmark.core.budget import FreeTierBudget
from chessmark.core.halt import SOURCE_FREE_TIER, Halt
from chessmark.db.enums import GameStatus
from chessmark.db.repositories import get_game
from chessmark.orchestration.worker import FREE_TIER_SPENT, HALTED, next_utc_midnight
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration

DAILY_CAP = (
    "litellm.RateLimitError: OpenrouterException - "
    '{"error":{"message":"Rate limit exceeded: free-models-per-day. '
    'Add 10 credits to unlock 1000 free model requests per day","code":429}}'
)

SHARED_POOL = (
    "litellm.RateLimitError - "
    '{"error":{"message":"Provider returned error","code":429,'
    '"metadata":{"limit_source":"upstream_provider_shared_pool",'
    '"provider_name":"Google AI Studio"}}}'
)


class _Headers:
    def __init__(self, **values: str) -> None:
        self._values = values

    def get(self, name: str) -> str | None:
        return self._values.get(name) or self._values.get(name.lower())


class _Response:
    def __init__(self, headers: _Headers | None = None) -> None:
        self.headers = headers


class _RateLimitedError(Exception):
    status_code = 429

    def __init__(self, message: str, response: _Response | None = None) -> None:
        super().__init__(message)
        self.response = response


# ====================================================================== telling them apart


def test_the_daily_cap_is_told_from_a_hot_pool() -> None:
    """Same status code, opposite meanings: one is our account, the other is one provider."""
    assert llm.is_free_daily_cap(_RateLimitedError(DAILY_CAP))
    assert not llm.is_free_daily_cap(_RateLimitedError(SHARED_POOL))


def test_a_hot_pool_is_still_a_hot_pool() -> None:
    """The path that works must keep working: rest the provider, not the whole harness."""
    limit = llm.rate_limit_from(_RateLimitedError(SHARED_POOL))

    assert limit.is_upstream_pool
    assert not limit.free_daily_cap
    assert limit.provider == "Google AI Studio"


def test_the_reset_time_is_read_from_the_header() -> None:
    """`X-RateLimit-Reset` is exact where the ladder guesses, and the cap can be hours out."""
    at = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)
    error = _RateLimitedError(
        DAILY_CAP, _Response(_Headers(**{"X-RateLimit-Reset": str(int(at.timestamp() * 1000))}))
    )

    assert llm.resets_at(error) == at


def test_seconds_and_milliseconds_are_both_understood() -> None:
    """OpenRouter sends milliseconds; the header is defined in neither unit by anyone. `1e11`
    separates them with room to spare — that is 1973 in ms and the year 5138 in seconds."""
    at = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)

    as_seconds = _RateLimitedError(
        DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp()))}))
    )
    as_millis = _RateLimitedError(
        DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp() * 1000))}))
    )

    assert llm.resets_at(as_seconds) == at
    assert llm.resets_at(as_millis) == at


def test_a_missing_or_nonsense_header_says_nothing() -> None:
    """`None` means "it did not say", which the caller replaces with a conservative fallback —
    not with a number invented here."""
    assert llm.resets_at(_RateLimitedError(DAILY_CAP)) is None
    assert (
        llm.resets_at(
            _RateLimitedError(DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": "soon"})))
        )
        is None
    )
    assert (
        llm.resets_at(
            _RateLimitedError(DAILY_CAP, _Response(_Headers(**{"x-ratelimit-reset": "0"})))
        )
        is None
    )


def test_the_fallback_is_the_next_utc_midnight() -> None:
    """Conservative on purpose: it can only be later than the true reset, so the worst case is
    waiting too long rather than resuming into a cap that has not lifted."""
    at = dt.datetime(2026, 8, 31, 23, 30, tzinfo=dt.UTC)

    assert next_utc_midnight(at) == dt.datetime(2026, 9, 1, 0, 0, tzinfo=dt.UTC)


# ====================================================================== what the worker does


async def test_the_daily_cap_halts_the_harness(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """One switch, not seventeen cooldowns discovered one request at a time."""

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(DAILY_CAP)

    worker = make_worker(capped)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == HALTED
    state = await worker.halt.state()
    assert state is not None
    assert state.source == SOURCE_FREE_TIER
    assert state.until is not None, "it lifts itself; nobody has to notice"


async def test_the_halt_expires_when_the_provider_said_it_would(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """The TTL *is* the expiry — nothing sweeps it, and there is no midnight job to fail."""
    at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=3)

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(
            DAILY_CAP,
            _Response(_Headers(**{"x-ratelimit-reset": str(int(at.timestamp() * 1000))})),
        )

    worker = make_worker(capped)
    worker.halt = Halt(redis)
    await worker.handle(game.first_job)

    ttl = await redis.ttl("chessmark:halt")
    assert 3 * 3600 - 120 < ttl <= 3 * 3600, f"the key should expire with the cap, got ttl={ttl}"


async def test_the_daily_cap_never_ends_a_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """Invariant 11 again. Running out of somebody else's free allowance is not a chess result."""

    async def capped(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(DAILY_CAP)

    worker = make_worker(capped)
    worker.halt = Halt(redis)
    await worker.handle(game.first_job)

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.RUNNING
    assert after.termination is None


async def test_a_hot_pool_still_pauses_one_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """The regression that would matter most: halting on every 429 would stop the whole site
    because one provider's free pool was busy for a minute."""

    async def hot(**_: Any) -> dict[str, Any]:
        raise _RateLimitedError(SHARED_POOL)

    worker = make_worker(hot)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert str(handled.outcome) == "paused"
    assert await worker.halt.state() is None, "one busy pool must not stop the harness"


# ====================================================================== the counter we keep


async def test_a_turn_is_not_run_once_the_allowance_is_spent(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counter gated *starting* a game and nothing else, so a pool already in flight spent
    past the allowance and then met the cap as a 429 (OPS-10)."""
    free_tier = FreeTierBudget(redis)
    await free_tier.record(free_tier.usable + 1)

    # The gate is per seat, so the seat has to be a free one. `seat_match` names its models
    # `scripted/white`, which draws on no allowance and is correctly exempt.
    monkeypatch.setattr(
        "chessmark.orchestration.worker.model_for", lambda _player: "vendor/model:free"
    )
    worker = make_worker(plays(["e4"]))
    worker.free_tier = free_tier

    handled = await worker.handle(game.first_job)

    assert handled.outcome == FREE_TIER_SPENT
    assert handled.result is None, "nothing was spent discovering what we already knew"

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.RUNNING, "it resumes when the allowance resets"


async def test_a_paid_model_is_not_stopped_by_a_free_allowance(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paid seat draws on no allowance, and stopping it would be an outage for a limit it is
    not subject to."""
    free_tier = FreeTierBudget(redis)
    await free_tier.record(free_tier.usable + 1)

    monkeypatch.setattr(
        "chessmark.orchestration.worker.model_for", lambda _player: "vendor/paid-model"
    )
    worker = make_worker(scripted(step(tool_call("make_move", move="e4"))))
    worker.free_tier = free_tier

    handled = await worker.handle(game.first_job)

    assert handled.outcome != FREE_TIER_SPENT
    assert handled.result is not None

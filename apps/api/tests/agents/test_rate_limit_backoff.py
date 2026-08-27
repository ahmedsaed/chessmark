"""Backing off when a provider says to.

A 429 is not a broken provider — it is a provider telling us when to come back. Treating it like
any other transient error meant backing off a maximum of eight seconds and giving up after four
tries, which against the free tier's shared pool cost ~20 doomed requests in a minute and then
abandoned the game at ply 0.

**The first fix went the wrong way, and this file used to assert it.** "A tournament has no
deadline, so waiting is free" is true of the *game* and false of the *request*: eight attempts here
produced eight doomed requests, the worker then requeued five times, and one game spent forty
requests over six and a half minutes before being abandoned anyway — 560 requests across one
incident, every one of them charged against the same daily allowance the patience was meant to
protect. Fourteen games in a row died that way.

So the direction is reversed. The gateway tries a *few* times, in case the pool clears in seconds,
and then gives up — and the game is **paused** rather than retried, which costs nothing at all
while it waits. Patience belongs in `core/cooldown.py`, where it is measured in minutes and spends
no requests to pass the time.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from chessmark.agents.llm import (
    LlmError,
    LlmGateway,
    RetryPolicy,
    is_rate_limit,
    is_retryable,
    rate_limit_from,
    retry_after_seconds,
)

#: The body OpenRouter actually returned for `z-ai/glm-5.2:free`, trimmed but not reshaped.
OPENROUTER_429 = (
    'litellm.RateLimitError: RateLimitError: OpenrouterException - {"error":{"message":'
    '"Provider returned error","code":429,"metadata":{"raw":"z-ai/glm-5.2:free is temporarily '
    'rate-limited upstream.","provider_name":"Decart","is_byok":false,"provider_error_code":'
    '"upstream_429","limit_source":"upstream_provider_shared_pool","retry_after_seconds":5,'
    '"retry_after_seconds_raw":5,"headers":{"Retry-After":"5"}}}}'
)


class RateLimitedError(Exception):
    """Shaped like the LiteLLM error, which carries its status on the exception itself."""

    def __init__(self, message: str = OPENROUTER_429, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


class BrokenError(Exception):
    def __init__(self) -> None:
        super().__init__("upstream exploded")
        self.status_code = 500


async def _no_sleep(_seconds: float) -> None:
    """Collapse the backoff, so the ladder is asserted rather than waited through."""
    return None


# ====================================================================== reading the provider


def test_a_rate_limit_is_recognised() -> None:
    assert is_rate_limit(RateLimitedError())
    assert is_retryable(RateLimitedError()), "and it is still worth retrying"
    assert not is_rate_limit(BrokenError())


def test_retry_after_is_read_out_of_the_error_body() -> None:
    """It arrives inside the message once LiteLLM has wrapped it, not as a header we can reach."""
    assert retry_after_seconds(RateLimitedError()) == 5.0


def test_retry_after_is_read_from_a_header_when_there_is_one() -> None:
    class WithHeadersError(Exception):
        status_code = 429

        class response:  # noqa: N801 - mimicking the provider library's shape
            headers: ClassVar[dict[str, str]] = {"retry-after": "42"}

    assert retry_after_seconds(WithHeadersError()) == 42.0


def test_an_error_that_says_nothing_asks_for_nothing() -> None:
    assert retry_after_seconds(BrokenError()) is None


# ====================================================================== the policy


def test_a_rate_limit_waits_as_long_as_the_provider_asked() -> None:
    """Better information than any formula we could invent."""
    policy = RetryPolicy(base_delay=0.5, max_delay=8.0, jitter=0.0)

    assert policy.delay_for(1, RateLimitedError()) == 5.0


def test_an_ordinary_failure_still_backs_off_exponentially_and_briefly() -> None:
    """Unchanged: a broken provider is not asking for patience, and waiting minutes on a 500
    would stall a live game a person is watching."""
    policy = RetryPolicy(base_delay=0.5, max_delay=8.0, jitter=0.0)

    assert [policy.delay_for(n, BrokenError()) for n in (1, 2, 3, 4, 5)] == [
        0.5,
        1.0,
        2.0,
        4.0,
        8.0,
    ]


def test_a_rate_limit_wait_is_capped() -> None:
    """A provider asking for an hour should not hold a worker for an hour."""
    policy = RetryPolicy(jitter=0.0, rate_limit_max_delay=120.0)

    class AsksForAgesError(RateLimitedError):
        def __init__(self) -> None:
            super().__init__('{"retry_after_seconds":9999}')

    assert policy.delay_for(1, AsksForAgesError()) == 120.0


def test_a_rate_limit_falls_back_to_backoff_when_the_provider_is_silent() -> None:
    """Silence is the **normal** case, not the exception: OpenRouter sends `Retry-After` only when
    every attempted provider returned a retry hint, and a free model served by a single endpoint
    that returned none carries nothing at all. Every one of the fourteen abandoned games looked
    like this.

    So the fallback ladder is the one that matters, and it climbs from its own base rather than
    from `base_delay`. That mattered in production: 0.5s doubling reached only 32 seconds in eight
    attempts, so `rate_limit_max_delay` never bound and a constant meant for connection errors was
    silently deciding how long to wait out a rate limit.
    """
    policy = RetryPolicy(jitter=0.0, rate_limit_base_delay=5.0, rate_limit_max_delay=300.0)

    class SilentError(RateLimitedError):
        def __init__(self) -> None:
            super().__init__("rate limited, no further detail")

    assert [policy.delay_for(n, SilentError()) for n in (1, 2, 3)] == [5.0, 10.0, 20.0]


def test_a_rate_limit_gets_its_own_attempt_budget() -> None:
    """Its own, and deliberately **smaller** than the default now.

    This test used to assert the opposite — eight attempts against four — and that is what the
    incident cost was made of. A rate limit is the one failure where trying again is *known* to be
    the wrong response, so the gateway makes a token effort and hands off to a pause. Retrying a
    500 is a hedge; retrying a 429 is ignoring what the provider just said.
    """
    policy = RetryPolicy(max_attempts=4, rate_limit_attempts=3)

    assert policy.attempts_for(BrokenError()) == 4
    assert policy.attempts_for(RateLimitedError()) == 3


# ====================================================================== end to end


async def test_the_gateway_waits_and_then_succeeds() -> None:
    """The behaviour that turns an abandoned game into a played one."""
    slept: list[float] = []
    attempts = 0

    async def flaky(**_: object) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise RateLimitedError()
        return {
            "id": "gen-1",
            "model": "vendor/model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def record(seconds: float) -> None:
        slept.append(seconds)

    gateway = LlmGateway(
        completion_fn=flaky,
        retry=RetryPolicy(jitter=0.0, rate_limit_attempts=8),
        sleep_fn=record,
    )

    completion = await gateway.complete(model="vendor/model", messages=[{"role": "user"}])

    assert completion.attempts == 4
    assert slept == [5.0, 5.0, 5.0], "each wait is the one the provider asked for"


async def test_the_gateway_gives_up_after_the_rate_limit_budget() -> None:
    """Patience is not infinite: a pool that is hot for an hour should free the worker."""

    async def always_limited(**_: object) -> dict[str, object]:
        raise RateLimitedError()

    async def instant(_: float) -> None:
        return None

    gateway = LlmGateway(
        completion_fn=always_limited,
        retry=RetryPolicy(max_attempts=2, rate_limit_attempts=5, jitter=0.0),
        sleep_fn=instant,
    )

    with pytest.raises(LlmError) as caught:
        await gateway.complete(model="vendor/model", messages=[{"role": "user"}])

    assert caught.value.attempts == 5, "the rate-limit budget, not the ordinary one"
    assert caught.value.status_code == 429


# ====================================================================== counting attempts


async def test_every_attempt_is_counted_including_the_ones_that_failed() -> None:
    """The whole basis of the free-tier guard.

    OpenRouter charges a request allowance for attempts, not for successes, and a failed call
    never reaches `llm_calls` — that row is written from a completion. So a count taken from the
    database misses exactly the retries a rate limit produces, which is why this is counted in the
    gateway instead. If it fired only on success the guard would be decorative.
    """
    counted: list[str] = []
    attempts = 0

    async def flaky(**_: object) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RateLimitedError()
        return {
            "id": "gen-1",
            "model": "vendor/model:free",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def record(model: str) -> None:
        counted.append(model)

    async def instant(_: float) -> None:
        return None

    gateway = LlmGateway(
        completion_fn=flaky,
        retry=RetryPolicy(jitter=0.0, rate_limit_attempts=8),
        sleep_fn=instant,
        on_attempt=record,
    )

    await gateway.complete(model="vendor/model:free", messages=[{"role": "user"}])

    assert counted == ["vendor/model:free"] * 3, "two failures and the success all cost a request"


async def test_attempts_are_counted_even_when_the_call_never_succeeds() -> None:
    """A game abandoned to rate limits still spent the allowance getting there."""
    counted: list[str] = []

    async def always_limited(**_: object) -> dict[str, object]:
        raise RateLimitedError()

    async def record(model: str) -> None:
        counted.append(model)

    async def instant(_: float) -> None:
        return None

    gateway = LlmGateway(
        completion_fn=always_limited,
        retry=RetryPolicy(max_attempts=2, rate_limit_attempts=4, jitter=0.0),
        sleep_fn=instant,
        on_attempt=record,
    )

    with pytest.raises(LlmError):
        await gateway.complete(model="vendor/model:free", messages=[{"role": "user"}])

    assert len(counted) == 4, "every doomed attempt was still a request"


# ====================================================================== what the orchestrator reads

#: The body from the incident, verbatim in shape: a single-endpoint free model, and **no retry hint
#: anywhere in it**. This is what fourteen consecutive abandoned games looked like.
SHARED_POOL_429 = (
    'litellm.RateLimitError: RateLimitError: OpenrouterException - {"error":{"message":'
    '"Provider returned error","code":429,"metadata":{"raw":"google/gemma-4-26b-a4b-it:free is '
    "temporarily rate-limited upstream. Please retry shortly, or add your own key to accumulate "
    'your rate limits","provider_name":"Google AI Studio","is_byok":false,"provider_error_code":'
    '"429","limit_source":"upstream_provider_shared_pool"}},"user_id":"user_3Hx"}'
)


class SharedPoolError(RateLimitedError):
    def __init__(self) -> None:
        super().__init__(SHARED_POOL_429)


def test_the_provider_and_the_limit_source_are_read_out() -> None:
    """Structured, because the orchestrator has to *act* on this — pause this game, cool this
    endpoint down — and a decision keyed off a substring search of an error string is a decision
    waiting to break the next time a provider rewords its 429."""
    limit = rate_limit_from(SharedPoolError())

    assert limit.provider == "Google AI Studio"
    assert limit.limit_source == "upstream_provider_shared_pool"
    assert limit.is_upstream_pool
    assert limit.retry_after_seconds is None, "the incident carried no hint, and that is normal"


def test_an_account_limit_is_told_apart_from_a_hot_pool() -> None:
    """They arrive as the same status code and call for different responses: waiting out a
    provider's pool is ours to do, while an account limit means stopping, not waiting."""
    assert not rate_limit_from(
        RateLimitedError('{"limit_source":"account_daily"}')
    ).is_upstream_pool


async def test_the_error_that_escapes_carries_the_rate_limit() -> None:
    """The gateway holds the provider's own exception and nothing downstream does. By the time an
    orchestrator sees a string the structure is gone — so it is attached on the way out."""

    async def always_limited(**_kwargs: object) -> dict[str, object]:
        raise SharedPoolError

    gateway = LlmGateway(
        completion_fn=always_limited,
        retry=RetryPolicy(rate_limit_attempts=2, rate_limit_base_delay=0.0, jitter=0.0),
        sleep_fn=_no_sleep,
    )

    with pytest.raises(LlmError) as raised:
        await gateway.complete(model="google/gemma-4-26b-a4b-it:free", messages=[])

    assert raised.value.rate_limit is not None
    assert raised.value.rate_limit.provider == "Google AI Studio"
    assert raised.value.status_code == 429


async def test_an_ordinary_failure_carries_no_rate_limit() -> None:
    """So the worker's branch cannot mistake an outage for a rate limit and pause on it. An outage
    should retry; a rate limit should wait."""

    async def always_broken(**_kwargs: object) -> dict[str, object]:
        raise BrokenError

    gateway = LlmGateway(
        completion_fn=always_broken,
        retry=RetryPolicy(max_attempts=1),
        sleep_fn=_no_sleep,
    )

    with pytest.raises(LlmError) as raised:
        await gateway.complete(model="a/b", messages=[])

    assert raised.value.rate_limit is None

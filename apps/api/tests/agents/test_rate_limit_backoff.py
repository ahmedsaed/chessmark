"""Backing off when a provider says to (Phase 13 prerequisite).

A 429 is not a broken provider — it is a provider telling us when to come back. Treating it like
any other transient error meant backing off a maximum of eight seconds and giving up after four
tries, which against the free tier's shared pool cost ~20 doomed requests in a minute and then
abandoned the game at ply 0. Twice, in one afternoon.

A tournament has no deadline, so waiting is free and correct.
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
    policy = RetryPolicy(base_delay=1.0, jitter=0.0, rate_limit_max_delay=300.0)

    class SilentError(RateLimitedError):
        def __init__(self) -> None:
            super().__init__("rate limited, no further detail")

    assert policy.delay_for(3, SilentError()) == 4.0


def test_a_rate_limit_gets_more_attempts_than_an_ordinary_failure() -> None:
    policy = RetryPolicy(max_attempts=4, rate_limit_attempts=8)

    assert policy.attempts_for(BrokenError()) == 4
    assert policy.attempts_for(RateLimitedError()) == 8


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

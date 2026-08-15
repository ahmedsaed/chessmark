"""The LLM gateway: retries, capture, and redaction.

Every test here replays a recorded fixture or a fake failure. Nothing reaches a provider, so the
suite is free to run and deterministic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from chessmark.agents.llm import LlmGateway, RetryPolicy, is_retryable
from chessmark.agents.pricing import ModelPricing, PricingTable
from chessmark.agents.redaction import REDACTED, contains_secret
from chessmark.agents.types import CostSource, LlmError
from tests.agents.cassettes import fails_with, load_all_cassettes, replay, responds_with

FAKE_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"


async def _no_sleep(_seconds: float) -> None:
    """Collapse backoff so retry tests run instantly."""
    return None


def gateway(**kwargs: Any) -> LlmGateway:
    kwargs.setdefault("api_key", FAKE_KEY)
    kwargs.setdefault("sleep_fn", _no_sleep)
    return LlmGateway(**kwargs)


# ------------------------------------------------------------------ happy path


async def test_a_recorded_tool_call_round_trips() -> None:
    result = await gateway(completion_fn=replay("openai_style_tool_call")).complete(
        model="openai/gpt-oss-20b:free",
        messages=[{"role": "user", "content": "Your move."}],
    )

    assert result.has_tool_calls
    assert result.tool_call("make_move") is not None
    assert result.usage.prompt > 0
    assert result.latency_ms >= 0
    assert result.attempts == 1


async def test_reasoning_is_carried_through_the_gateway() -> None:
    result = await gateway(completion_fn=replay("nvidia_reasoning_tool_call")).complete(
        model="nvidia/nemotron-nano-9b-v2:free",
        messages=[{"role": "user", "content": "Your move."}],
    )

    assert result.reasoning
    assert result.usage.reasoning > 0


async def test_missing_tool_call_is_returned_not_raised() -> None:
    """A model answering in prose is a benchmark finding for the turn loop to handle."""
    result = await gateway(completion_fn=replay("prose_no_tool_call")).complete(
        model="synthetic/chatty-model", messages=[]
    )

    assert not result.has_tool_calls
    assert result.content is not None


# ------------------------------------------------------------------ the request


def test_openrouter_prefix_is_applied_to_vendor_slugs() -> None:
    """Every OpenRouter id contains a slash, so the prefix must be tested for explicitly —
    otherwise LiteLLM reads the vendor half as its own provider name and routes elsewhere."""
    request = gateway().build_request(model="nvidia/nemotron-nano-9b-v2:free", messages=[])
    assert request["model"] == "openrouter/nvidia/nemotron-nano-9b-v2:free"


def test_an_already_prefixed_model_is_left_alone() -> None:
    request = gateway().build_request(model="openrouter/openai/gpt-oss-20b:free", messages=[])
    assert request["model"] == "openrouter/openai/gpt-oss-20b:free"


def test_usage_accounting_is_requested() -> None:
    """Asking OpenRouter to report its charge is what makes cost measured, not estimated."""
    request = gateway().build_request(model="a/b", messages=[])
    assert request["extra_body"] == {"usage": {"include": True}}


def test_tools_imply_a_tool_choice() -> None:
    tool = {"type": "function", "function": {"name": "make_move", "parameters": {}}}
    request = gateway().build_request(model="a/b", messages=[], tools=[tool])

    assert request["tools"] == [tool]
    assert request["tool_choice"] == "auto"


def test_no_tools_means_no_tool_choice() -> None:
    assert "tool_choice" not in gateway().build_request(model="a/b", messages=[])


# ------------------------------------------------------------------ redaction


async def test_the_api_key_never_reaches_the_stored_request() -> None:
    """LOG-01 stores requests verbatim. Verbatim must never include the credential."""
    result = await gateway(completion_fn=replay("openai_style_tool_call")).complete(
        model="openai/gpt-oss-20b:free",
        messages=[{"role": "user", "content": "Your move."}],
    )

    assert not contains_secret(result.request)
    assert FAKE_KEY not in str(result.request)
    assert not contains_secret(result.response)


async def test_a_key_pasted_into_a_message_is_scrubbed() -> None:
    result = await gateway(completion_fn=replay("openai_style_tool_call")).complete(
        model="openai/gpt-oss-20b:free",
        messages=[{"role": "user", "content": f"my key is {FAKE_KEY} please help"}],
    )

    stored = str(result.request)
    assert FAKE_KEY not in stored
    assert REDACTED in stored


async def test_a_key_echoed_in_a_response_is_scrubbed() -> None:
    leaky = {
        "model": "x/y",
        "choices": [{"message": {"content": f"your key {FAKE_KEY} is invalid"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    result = await gateway(completion_fn=responds_with(leaky)).complete(model="x/y", messages=[])

    assert not contains_secret(result.response)


def test_no_stored_fixture_contains_a_credential() -> None:
    """Fixtures are committed to git; a key in one is a key in the repository history."""
    for cassette in load_all_cassettes():
        assert not contains_secret(cassette.request), f"{cassette.name} request"
        assert not contains_secret(cassette.response), f"{cassette.name} response"


# ------------------------------------------------------------------ cost


async def test_provider_cost_is_preferred() -> None:
    payload = {
        "model": "x/y",
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": "0.0042"},
    }
    pricing = PricingTable(
        {"x/y": ModelPricing(model="x/y", prompt_usd_per_token=Decimal("0.001"))}
    )

    result = await gateway(completion_fn=responds_with(payload), pricing=pricing).complete(
        model="x/y", messages=[]
    )

    assert result.cost_usd == Decimal("0.0042")
    assert result.cost_source is CostSource.PROVIDER


async def test_cost_falls_back_to_the_pricing_table() -> None:
    payload = {
        "model": "x/y",
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
    }
    pricing = PricingTable(
        {
            "x/y": ModelPricing(
                model="x/y",
                prompt_usd_per_token=Decimal("0.000001"),
                completion_usd_per_token=Decimal("0.000002"),
            )
        }
    )

    result = await gateway(completion_fn=responds_with(payload), pricing=pricing).complete(
        model="x/y", messages=[]
    )

    assert result.cost_usd == Decimal("0.0012")
    assert result.cost_source is CostSource.COMPUTED


async def test_unpriced_model_is_flagged_unknown() -> None:
    payload = {
        "model": "x/y",
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 1},
    }
    result = await gateway(completion_fn=responds_with(payload)).complete(model="x/y", messages=[])

    assert result.cost_source is CostSource.UNKNOWN


# ------------------------------------------------------------------ retries


class TransientError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"boom {status}")
        self.status_code = status


class BadRequestError(Exception):
    """Named to match LiteLLM's class, which is how the classifier recognises it."""


class RateLimitError(Exception):
    pass


class AuthenticationError(Exception):
    pass


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(status: int) -> None:
    assert is_retryable(TransientError(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(status: int) -> None:
    assert not is_retryable(TransientError(status))


def test_classification_is_by_exception_name_when_there_is_no_status() -> None:
    assert is_retryable(RateLimitError("slow down"))
    assert not is_retryable(BadRequestError("bad"))
    assert not is_retryable(AuthenticationError("nope"))


def test_an_unrecognised_error_is_treated_as_fatal() -> None:
    """Erring toward not retrying: a retry loop on a permanent failure just spends more."""
    assert not is_retryable(ValueError("who knows"))


async def test_a_server_error_retries_then_succeeds() -> None:
    """AGENT-09: transient provider failures must not end a game."""
    completion_fn = fails_with(TransientError(500), TransientError(503))

    result = await gateway(completion_fn=completion_fn).complete(model="x/y", messages=[])

    assert result.content == "recovered"
    assert result.attempts == 3
    assert completion_fn.calls["count"] == 3  # type: ignore[attr-defined]


async def test_a_bad_request_does_not_retry() -> None:
    completion_fn = fails_with(BadRequestError("malformed tool schema"))

    with pytest.raises(LlmError) as caught:
        await gateway(completion_fn=completion_fn).complete(model="x/y", messages=[])

    assert caught.value.attempts == 1
    assert not caught.value.retryable
    assert completion_fn.calls["count"] == 1  # type: ignore[attr-defined]


async def test_retries_are_bounded() -> None:
    completion_fn = fails_with(*[TransientError(500) for _ in range(10)])
    policy = RetryPolicy(max_attempts=3)

    with pytest.raises(LlmError) as caught:
        await gateway(completion_fn=completion_fn, retry=policy).complete(model="x/y", messages=[])

    assert caught.value.attempts == 3
    assert completion_fn.calls["count"] == 3  # type: ignore[attr-defined]


async def test_a_failed_call_still_reports_a_redacted_request() -> None:
    """A failure must be as debuggable as a success, and just as safe to store."""
    with pytest.raises(LlmError) as caught:
        await gateway(completion_fn=fails_with(BadRequestError("bad"))).complete(
            model="x/y", messages=[{"role": "user", "content": FAKE_KEY}]
        )

    assert caught.value.request
    assert not contains_secret(caught.value.request)


def test_backoff_grows_and_is_bounded() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=4.0, jitter=0.0)

    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(9) == 4.0, "must be capped"


def test_backoff_jitter_stays_within_bounds() -> None:
    """Jitter keeps concurrent workers from retrying in lockstep."""
    policy = RetryPolicy(base_delay=1.0, jitter=0.25)
    delays = [policy.delay_for(1) for _ in range(50)]

    assert all(1.0 <= delay <= 1.25 for delay in delays)
    assert len(set(delays)) > 1, "jitter should actually vary"


# ------------------------------------------------------------------ deadlines


async def test_a_call_that_overruns_the_deadline_is_cut_off() -> None:
    """Found live: one call generated for 1,093 seconds against a 180-second setting. The timeout
    was passed to the provider library and simply not honoured. A deadline only the callee
    enforces is not a deadline."""
    import asyncio

    async def never_returns(**_kwargs: Any) -> Any:
        await asyncio.sleep(30)

    with pytest.raises(LlmError) as caught:
        await gateway(completion_fn=never_returns).complete(
            model="x/y", messages=[], deadline_seconds=0.05
        )

    assert "exceeded" in str(caught.value)


async def test_an_overrun_is_not_retried() -> None:
    """A call that ran to the deadline was producing tokens the whole time, not stalled. Retrying
    spends the same wall clock to reach the same place."""
    import asyncio

    calls = {"n": 0}

    async def slow(**_kwargs: Any) -> Any:
        calls["n"] += 1
        await asyncio.sleep(30)

    with pytest.raises(LlmError) as caught:
        await gateway(completion_fn=slow).complete(model="x/y", messages=[], deadline_seconds=0.05)

    assert calls["n"] == 1
    assert not caught.value.retryable


async def test_a_call_inside_the_deadline_is_untouched() -> None:
    result = await gateway(completion_fn=replay("openai_style_tool_call")).complete(
        model="openai/gpt-oss-20b:free", messages=[], deadline_seconds=30
    )
    assert result.has_tool_calls

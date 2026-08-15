"""Provider responses, normalised into one shape.

Driven entirely by recorded and hand-authored fixtures — no network, no spend, deterministic.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from chessmark.agents.normalise import (
    extract_provider_cost,
    extract_reasoning,
    extract_tool_calls,
    extract_usage,
    normalise_response,
)
from tests.agents.cassettes import cassette_names, load_all_cassettes, load_cassette

# --------------------------------------------------------------- across every fixture


def test_fixtures_exist() -> None:
    """Guard against the parametrised tests below silently covering nothing."""
    assert len(cassette_names()) >= 4


@pytest.mark.parametrize("name", cassette_names())
def test_every_fixture_normalises_without_raising(name: str) -> None:
    parsed = normalise_response(load_cassette(name).response)

    assert parsed.usage.prompt >= 0
    assert parsed.usage.completion >= 0
    assert parsed.usage.cached <= parsed.usage.prompt


def test_at_least_two_fixtures_are_real_recordings() -> None:
    """Synthetic shapes are a fallback for what we cannot reach, not the whole suite."""
    live = [c for c in load_all_cassettes() if c.is_live_recording]
    assert len(live) >= 2, "the normaliser must be proven against real provider output"


def test_synthetic_fixtures_say_so() -> None:
    for cassette in load_all_cassettes():
        if not cassette.is_live_recording:
            assert "HAND-AUTHORED" in cassette.note, (
                f"{cassette.name} is synthetic but does not say so — a reader must never mistake "
                "a written-from-the-docs shape for a recorded one"
            )


# --------------------------------------------------------------- OpenAI-style (live)


def test_openai_style_tool_call() -> None:
    parsed = normalise_response(load_cassette("openai_style_tool_call").response)

    assert len(parsed.tool_calls) == 1
    call = parsed.tool_calls[0]
    assert call.name == "make_move"
    assert call.ok
    assert "move" in call.arguments
    assert parsed.finish_reason == "tool_calls"
    assert parsed.usage.prompt > 0
    assert parsed.usage.completion > 0


# --------------------------------------------------------------- reasoning model (live)


def test_reasoning_trace_is_captured() -> None:
    """AGENT-07: reasoning is stored whenever the provider exposes it."""
    parsed = normalise_response(load_cassette("nvidia_reasoning_tool_call").response)

    assert parsed.reasoning, "the recorded reasoning model returned a trace and we dropped it"
    assert parsed.usage.reasoning > 0, "reasoning tokens must be counted separately"
    assert len(parsed.tool_calls) == 1


# --------------------------------------------------------------- Anthropic-style (synthetic)


def test_anthropic_style_usage_field_names() -> None:
    """Anthropic names its token fields differently; the counts must still land."""
    parsed = normalise_response(load_cassette("anthropic_style_cache_hit").response)

    assert parsed.usage.prompt == 18402
    assert parsed.usage.completion == 340
    assert parsed.usage.cached == 15462


def test_anthropic_style_thinking_blocks_become_reasoning() -> None:
    parsed = normalise_response(load_cassette("anthropic_style_cache_hit").response)

    assert parsed.reasoning is not None
    assert "Nf3" in parsed.reasoning


def test_anthropic_style_content_blocks_become_text() -> None:
    parsed = normalise_response(load_cassette("anthropic_style_cache_hit").response)
    assert parsed.content == "Developing the knight."


def test_stop_reason_is_read_as_finish_reason() -> None:
    parsed = normalise_response(load_cassette("anthropic_style_cache_hit").response)
    assert parsed.finish_reason == "tool_use"


def test_cache_hit_rate_is_derived() -> None:
    """NFR-06 targets >80%; the number has to come from somewhere."""
    parsed = normalise_response(load_cassette("anthropic_style_cache_hit").response)
    assert parsed.usage.cache_hit_rate == pytest.approx(0.84, abs=0.01)
    assert parsed.usage.uncached_prompt == 18402 - 15462


# --------------------------------------------------------------- failure shapes


def test_malformed_tool_arguments_surface_as_data() -> None:
    """A model emitting broken JSON is a finding to count, not an exception to raise."""
    parsed = normalise_response(load_cassette("malformed_tool_arguments").response)

    assert len(parsed.tool_calls) == 1
    call = parsed.tool_calls[0]
    assert not call.ok
    assert call.parse_error is not None
    assert call.arguments == {}
    assert call.raw_arguments == "{move: Nf3"


def test_prose_response_has_no_tool_calls() -> None:
    """AGENT-01 forbids parsing a move out of prose, so this must stay empty."""
    parsed = normalise_response(load_cassette("prose_no_tool_call").response)

    assert parsed.tool_calls == []
    assert parsed.content is not None
    assert parsed.usage.cached == 384


# --------------------------------------------------------------- tolerance


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": None, "tool_calls": None}}]},
        {"choices": "not a list"},
        {"usage": "not a dict"},
        {"choices": [{"message": {"tool_calls": ["not a dict"]}}]},
        {"choices": [{"message": {"tool_calls": [{"function": None}]}}]},
    ],
)
def test_degenerate_payloads_do_not_raise(payload: dict[str, object]) -> None:
    """A model that returns nothing useful is a benchmark observation, not a crash."""
    parsed = normalise_response(payload)  # type: ignore[arg-type]

    assert parsed.tool_calls == []
    assert parsed.usage.prompt == 0


def test_blank_content_is_normalised_to_none() -> None:
    parsed = normalise_response({"choices": [{"message": {"content": "   "}}]})
    assert parsed.content is None


def test_cached_tokens_are_clamped_to_the_prompt() -> None:
    """Some providers report cache reads in addition to prompt tokens rather than within them."""
    usage = extract_usage({"usage": {"prompt_tokens": 100, "cache_read_input_tokens": 400}})

    assert usage.cached == 100
    assert usage.uncached_prompt == 0
    assert usage.cache_hit_rate == 1.0


def test_cache_hit_rate_of_an_empty_prompt_is_zero() -> None:
    assert extract_usage({}).cache_hit_rate == 0.0


def test_reasoning_absent_returns_none() -> None:
    assert extract_reasoning({"content": "hello"}) is None
    assert extract_reasoning({"reasoning_content": "   "}) is None


def test_reasoning_from_provider_specific_fields() -> None:
    message = {"provider_specific_fields": {"reasoning_content": "thinking about d4"}}
    assert extract_reasoning(message) == "thinking about d4"


def test_tool_calls_absent_returns_empty() -> None:
    assert extract_tool_calls({"content": "hi"}) == []


def test_non_object_tool_arguments_are_rejected() -> None:
    calls = extract_tool_calls(
        {"tool_calls": [{"id": "1", "function": {"name": "make_move", "arguments": "[1,2]"}}]}
    )
    assert not calls[0].ok
    assert "expected a JSON object" in (calls[0].parse_error or "")


def test_empty_tool_arguments_are_rejected() -> None:
    calls = extract_tool_calls(
        {"tool_calls": [{"id": "1", "function": {"name": "resign", "arguments": ""}}]}
    )
    assert not calls[0].ok
    assert calls[0].parse_error == "empty arguments"


# --------------------------------------------------------------- provider cost


def test_provider_reported_cost_is_read() -> None:
    cost = extract_provider_cost({"usage": {"cost": "0.00123"}})
    assert cost == Decimal("0.00123")


def test_missing_provider_cost_returns_none() -> None:
    assert extract_provider_cost({"usage": {"prompt_tokens": 10}}) is None
    assert extract_provider_cost({}) is None


def test_unparseable_provider_cost_returns_none() -> None:
    assert extract_provider_cost({"usage": {"cost": "free"}}) is None


def test_live_fixtures_report_a_provider_cost() -> None:
    """OpenRouter returns its own charge when asked, which is what we bill against."""
    for cassette in load_all_cassettes():
        if cassette.is_live_recording:
            assert extract_provider_cost(cassette.response) is not None, (
                f"{cassette.name} has no provider cost — is `usage: {{include: true}}` still sent?"
            )

"""A streamed call must produce the same record as one that did not stream (ADR-0035).

Streaming is the rung of ADR-0035 that can lose the record, which is why it ships off. LiteLLM's
streaming path reads `reasoning_content` and drops `reasoning`, so on several providers the model's
thinking never arrives at all — GLM-5 and every vLLM-backed endpoint
([#21386](https://github.com/BerriAI/litellm/issues/21386),
[#20246](https://github.com/BerriAI/litellm/issues/20246)). That is the worst shape a bug can take
here: an absent reasoning field is indistinguishable from a model that did not reason, so invariant
3 breaks in the one direction nothing downstream can flag.

Everything below is about the reassembly being *indistinguishable* from a whole response. If it is,
the rest of the system — costing, `llm_calls`, the tool loop, compaction — cannot tell, which is
the only basis on which this may be turned on at all.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from chessmark.agents.llm import LlmGateway, collect_stream
from chessmark.agents.normalise import normalise_response


async def _chunks(pieces: list[dict[str, Any]]) -> Any:
    for piece in pieces:
        yield piece


def _delta(**delta: Any) -> dict[str, Any]:
    return {"model": "vendor/m", "choices": [{"index": 0, "delta": delta}]}


# ====================================================================== reassembly


async def test_content_fragments_rejoin_in_order() -> None:
    collected = await collect_stream(
        _chunks([_delta(content="Play "), _delta(content="e4"), _delta(content=".")])
    )

    assert collected["choices"][0]["message"]["content"] == "Play e4."


async def test_reasoning_survives_under_either_spelling() -> None:
    """**The bug this file exists for.** A provider sends `reasoning`, another sends
    `reasoning_content`, and reading only one of them loses the whole chain of thought for half the
    catalogue — silently, because the field is simply absent."""
    modern = await collect_stream(
        _chunks([_delta(reasoning="I should "), _delta(reasoning="move.")])
    )
    legacy = await collect_stream(
        _chunks([_delta(reasoning_content="I should "), _delta(reasoning_content="move.")])
    )

    assert modern["choices"][0]["message"]["reasoning"] == "I should move."
    assert legacy["choices"][0]["message"]["reasoning"] == "I should move."


async def test_a_tool_call_is_rebuilt_from_its_fragments() -> None:
    """Arguments arrive a few characters at a time, keyed by index. Concatenating by index is the
    only way back to a call the dispatcher can run — anything else produces `{"mo` and a crash."""
    collected = await collect_stream(
        _chunks(
            [
                _delta(
                    tool_calls=[
                        {"index": 0, "id": "c1", "function": {"name": "make_move", "arguments": ""}}
                    ]
                ),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"mo'}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": 've": "e4"}'}}]),
            ]
        )
    )

    parsed = normalise_response(collected)

    assert [(c.name, c.arguments) for c in parsed.tool_calls] == [("make_move", {"move": "e4"})]


async def test_two_tool_calls_do_not_merge() -> None:
    """Two calls in one response are two indices. Ignoring the index concatenates them into one
    call whose arguments are unparseable."""
    collected = await collect_stream(
        _chunks(
            [
                _delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "a",
                            "function": {"name": "get_board", "arguments": "{}"},
                        },
                        {"index": 1, "id": "b", "function": {"name": "say", "arguments": "{}"}},
                    ]
                )
            ]
        )
    )

    parsed = normalise_response(collected)

    assert [c.name for c in parsed.tool_calls] == ["get_board", "say"]


async def test_usage_from_the_final_chunk_is_what_gets_costed() -> None:
    """**Invariant 4.** Usage arrives only in the last chunk, and only when
    `stream_options.include_usage` was sent. Dropping it costs every streamed call at zero — a
    number, so nothing downstream would ever call it wrong."""
    collected = await collect_stream(
        _chunks(
            [
                _delta(content="e4"),
                {
                    "model": "vendor/m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 12, "total_tokens": 912},
                },
            ]
        )
    )

    parsed = normalise_response(collected)

    assert parsed.usage.prompt == 900
    assert parsed.usage.completion == 12
    assert parsed.finish_reason == "tool_calls"


async def test_a_streamed_response_normalises_like_a_whole_one() -> None:
    """The property everything else rests on: `normalise_response` must not be able to tell.

    Asserted against the same content delivered both ways, because "it parses" is much weaker than
    "it parses to the same thing".
    """
    whole = {
        "model": "vendor/m",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Playing e4.",
                    "reasoning": "Center control.",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "make_move", "arguments": '{"move": "e4"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 12},
    }
    streamed = await collect_stream(
        _chunks(
            [
                _delta(reasoning="Center control."),
                _delta(content="Playing e4."),
                _delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "make_move", "arguments": '{"move": "e4"}'},
                        }
                    ]
                ),
                {
                    "model": "vendor/m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 12},
                },
            ]
        )
    )

    a, b = normalise_response(whole), normalise_response(streamed)

    assert (a.content, a.reasoning, a.finish_reason) == (b.content, b.reasoning, b.finish_reason)
    assert (a.usage.prompt, a.usage.completion) == (b.usage.prompt, b.usage.completion)
    assert [(c.name, c.arguments) for c in a.tool_calls] == [
        (c.name, c.arguments) for c in b.tool_calls
    ]


# ====================================================================== the fragments


async def test_fragments_are_reported_as_they_arrive() -> None:
    """What makes a 369-second round readable while it is happening. Reasoning and output are
    reported apart, because they are different registers and a fragment that did not say which it
    was could only be appended to the wrong block."""
    seen: list[tuple[str, str]] = []

    async def on_token(kind: str, text: str) -> None:
        seen.append((kind, text))

    await collect_stream(
        _chunks([_delta(reasoning="I "), _delta(reasoning="think"), _delta(content="e4")]),
        on_token,
    )

    assert seen == [("reasoning", "I "), ("reasoning", "think"), ("output", "e4")]


# ====================================================================== the gateway


async def test_the_gateway_does_not_stream_unless_asked() -> None:
    """**Off by default, and it is a record decision.** A gateway that streamed without being told
    to would silently change what is stored for every model whose reasoning LiteLLM drops."""
    sent: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        sent.update(kwargs)
        return {
            "model": "vendor/m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "e4"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    gateway = LlmGateway(completion_fn=fake)
    await gateway.complete(model="vendor/m", messages=[{"role": "user", "content": "go"}])

    assert "stream" not in sent


async def test_streaming_asks_for_usage_with_it() -> None:
    """Without `include_usage` the final chunk carries no usage and every streamed call is free
    (invariant 4). The two flags are one decision and are sent together."""
    sent: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        sent.update(kwargs)
        return _chunks(
            [
                _delta(content="e4"),
                {
                    "model": "vendor/m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
            ]
        )

    gateway = LlmGateway(completion_fn=fake, stream=True)
    completion = await gateway.complete(
        model="vendor/m", messages=[{"role": "user", "content": "go"}]
    )

    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert completion.content == "e4"
    assert completion.usage.prompt == 5


async def test_a_streamed_call_is_costed_from_its_reported_usage() -> None:
    """Invariant 4 end to end: the money comes from the counts the provider reported, by the same
    path a non-streamed call uses."""
    from chessmark.agents.pricing import ModelPricing, PricingTable

    async def fake(**kwargs: Any) -> Any:
        return _chunks(
            [
                _delta(content="e4"),
                {
                    "model": "vendor/m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                },
            ]
        )

    pricing = PricingTable(
        {
            "vendor/m": ModelPricing(
                model="vendor/m",
                prompt_usd_per_token=Decimal("0.000001"),
                completion_usd_per_token=Decimal("0.000002"),
            )
        }
    )
    gateway = LlmGateway(completion_fn=fake, stream=True, pricing=pricing)

    completion = await gateway.complete(
        model="vendor/m", messages=[{"role": "user", "content": "go"}]
    )

    assert completion.cost_usd > 0
    assert completion.cost_usd == Decimal("1000") * Decimal("0.000001") + Decimal("100") * Decimal(
        "0.000002"
    )


# ====================================================================== the guard


def _streamed(reasoning_tokens: int, reasoning: str | None) -> Any:
    """A streamed response reporting `reasoning_tokens` and carrying (or not) the text."""

    async def fake(**kwargs: Any) -> Any:
        return _chunks(
            [
                *([_delta(reasoning=reasoning)] if reasoning else []),
                _delta(content="e4"),
                {
                    "model": "vendor/m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
                    },
                },
            ]
        )

    return fake


async def test_an_endpoint_that_loses_its_thinking_stops_streaming() -> None:
    """**The check that makes streaming safe to run at all, and it is exact rather than a guess.**

    `usage.reasoning` is the provider's own count of the tokens it spent thinking, and it is
    billed. A response reporting thousands of them and carrying no reasoning text is one where the
    text existed and we failed to collect it — which on the streaming path is LiteLLM reading
    `reasoning_content` and discarding `reasoning`. Without this the loss is invisible: an absent
    reasoning field looks exactly like a model that did not reason.
    """
    gateway = LlmGateway(completion_fn=_streamed(2048, None), stream=True)

    await gateway.complete(model="vendor/m", messages=[])

    assert gateway.dropped_reasoning, "a call that lost its thinking went unnoticed"


async def test_a_healthy_endpoint_keeps_streaming() -> None:
    """Reasoning tokens *and* the text is the normal case, and must not trip the guard."""
    gateway = LlmGateway(completion_fn=_streamed(2048, "I should move."), stream=True)

    completion = await gateway.complete(model="vendor/m", messages=[])

    assert completion.reasoning == "I should move."
    assert not gateway.dropped_reasoning


async def test_a_model_that_never_reasons_keeps_streaming() -> None:
    """No reasoning tokens and no text is a model that does not reason, not a loss. Tripping here
    would take every non-reasoning model off the streaming path for nothing."""
    gateway = LlmGateway(completion_fn=_streamed(0, None), stream=True)

    await gateway.complete(model="vendor/m", messages=[])

    assert not gateway.dropped_reasoning


async def test_the_next_call_to_that_endpoint_is_not_streamed() -> None:
    """A ratchet, and the point of it: the endpoint goes back to whole responses, so the *second*
    call already has its reasoning again. One call is the whole cost of learning this."""
    calls: list[dict[str, Any]] = []

    async def fake(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs.get("stream"):
            return await _streamed(2048, None)(**kwargs)
        return {
            "model": "vendor/m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "reasoning": "recovered", "content": "e4"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "completion_tokens_details": {"reasoning_tokens": 2048},
            },
        }

    gateway = LlmGateway(completion_fn=fake, stream=True)

    first = await gateway.complete(model="vendor/m", messages=[])
    second = await gateway.complete(model="vendor/m", messages=[])

    assert first.reasoning is None
    assert second.reasoning == "recovered", "the endpoint was not taken off the streaming path"
    assert [bool(c.get("stream")) for c in calls] == [True, False]


async def test_the_verdict_is_per_endpoint_not_per_model() -> None:
    """The same weights served by two providers are two implementations, and only one of them may
    be losing the thinking. Keying by model alone would punish the healthy one."""
    from chessmark.agents.routing import ProviderRouting

    broken = LlmGateway(
        completion_fn=_streamed(2048, None),
        stream=True,
        routing=ProviderRouting(only=["BadHost"]),
    )
    await broken.complete(model="vendor/m", messages=[])

    assert broken.dropped_reasoning == {"vendor/m@BadHost"}


async def test_a_whole_response_that_hides_its_reasoning_is_not_evidence() -> None:
    """A non-streamed call is the reference, so it cannot be the thing that condemns an endpoint —
    a model that hides its thinking reports exactly this shape and is working correctly."""
    gateway = LlmGateway(completion_fn=_streamed(2048, None), stream=False)

    async def whole(**kwargs: Any) -> Any:
        return {
            "model": "vendor/m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "e4"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "completion_tokens_details": {"reasoning_tokens": 2048},
            },
        }

    gateway = LlmGateway(completion_fn=whole, stream=False)
    await gateway.complete(model="vendor/m", messages=[])

    assert not gateway.dropped_reasoning

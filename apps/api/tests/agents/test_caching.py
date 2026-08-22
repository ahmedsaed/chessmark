"""Prompt-cache breakpoints.

ADR-0003's whole cost argument assumed caching is automatic. It is for OpenAI, DeepSeek, Moonshot
and Grok — and it is not for Anthropic or Alibaba, which cache only what a `cache_control`
breakpoint marks. The first Claude game cost **$1.40 against its opponent's $0.11** for the same 61
plies, and burned its per-game cap without reaching a result.

These tests pin the request shape. They cannot prove a provider actually caches — that needs a live
call, and the suite never makes one — so the live confirmation is a recorded number in the roadmap,
not an assertion here.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from chessmark.agents.caching import (
    CACHE_CONTROL,
    apply_cache_control,
    needs_explicit_cache,
    vendor_of,
)


def transcript() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are playing as White."},
        {"role": "user", "content": "It is your move."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "1", "name": "make_move", "content": '{"ok": true}'},
        {"role": "user", "content": "Opponent played e5."},
    ]


def marks(messages: list[dict[str, Any]]) -> list[int]:
    """Indexes carrying a cache breakpoint."""
    found = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if isinstance(content, list) and any("cache_control" in block for block in content):
            found.append(index)
    return found


# ====================================================================== who needs it


@pytest.mark.parametrize("slug", ["anthropic/claude-haiku-4.5", "qwen/qwen3-max-thinking"])
def test_explicit_cache_vendors_are_marked(slug: str) -> None:
    """The two that were observed at a 0% hit rate over hundreds of thousands of tokens."""
    assert needs_explicit_cache(slug)
    assert marks(apply_cache_control(transcript(), model_slug=slug))


@pytest.mark.parametrize(
    "slug",
    [
        "openai/gpt-5.4-mini",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k2.5",
        "x-ai/grok-4.3",
        "z-ai/glm-5",
    ],
)
def test_implicit_cache_vendors_are_left_alone(slug: str) -> None:
    """They cache without being asked — `gpt-5.4-mini` reached 95% untouched. Sending content
    blocks they did not ask for is a gratuitous difference in request shape."""
    assert not needs_explicit_cache(slug)

    messages = transcript()
    assert apply_cache_control(messages, model_slug=slug) == messages


def test_google_is_marked_despite_caching_implicitly() -> None:
    """Gemini caches implicitly only above a minimum prefix, which is why its hit rate ran 24% on a
    39-ply game and 77% on an 80-ply one. Explicit breakpoints cover the early plies."""
    assert needs_explicit_cache("google/gemini-3.7-flash")


def test_the_vendor_is_read_from_the_slug() -> None:
    assert vendor_of("anthropic/claude-haiku-4.5") == "anthropic"
    assert vendor_of("~deepseek/deepseek-v4-flash-latest") == "deepseek"
    assert vendor_of("Qwen/Qwen3-Max") == "qwen"


# ====================================================================== where the marks go


def test_the_system_prompt_is_always_a_breakpoint() -> None:
    """It is fixed and versioned for the life of a game (invariant 2), so it is the one block
    guaranteed worth caching from ply one."""
    marked = apply_cache_control(transcript(), model_slug="anthropic/claude-haiku-4.5")

    assert 0 in marks(marked)
    assert marked[0]["content"][0]["text"] == "You are playing as White."


def test_the_second_breakpoint_rides_the_end_of_the_history() -> None:
    """So each turn extends the cached prefix instead of starting a new one."""
    marked = apply_cache_control(transcript(), model_slug="anthropic/claude-haiku-4.5")

    assert marks(marked) == [0, 4]


def test_no_more_than_two_breakpoints_are_used() -> None:
    """Anthropic allows four. Spending them all here would leave none for anything added later,
    and two is what an append-only transcript actually needs."""
    long_transcript = transcript() + [{"role": "user", "content": f"move {n}"} for n in range(50)]

    assert len(marks(apply_cache_control(long_transcript, model_slug="qwen/qwen3-max"))) == 2


def test_a_tool_call_only_message_is_not_marked() -> None:
    """There is no text block to attach a breakpoint to, and inventing an empty one would change
    the prefix for no benefit."""
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
    ]

    marked = apply_cache_control(messages, model_slug="anthropic/claude-haiku-4.5")

    assert marks(marked) == [0]


def test_a_single_message_gets_one_breakpoint_not_two() -> None:
    """The first turn of a game is the system prompt alone. Marking it twice would be wrong."""
    marked = apply_cache_control(
        [{"role": "system", "content": "system"}], model_slug="anthropic/claude-haiku-4.5"
    )

    assert marks(marked) == [0]


def test_an_empty_transcript_is_handled() -> None:
    assert apply_cache_control([], model_slug="anthropic/claude-haiku-4.5") == []


# ====================================================================== shape and purity


def test_the_marker_is_the_shape_both_vendors_accept() -> None:
    marked = apply_cache_control(transcript(), model_slug="anthropic/claude-haiku-4.5")
    block = marked[0]["content"][0]

    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert CACHE_CONTROL == {"type": "ephemeral"}


def test_the_input_is_never_mutated() -> None:
    """The caller stores and logs the same list. A breakpoint leaking into the stored transcript
    would misrepresent what was sent as what was recorded (LOG-01)."""
    original = transcript()
    untouched = copy.deepcopy(original)

    apply_cache_control(original, model_slug="anthropic/claude-haiku-4.5")

    assert original == untouched


def test_marking_preserves_the_text_exactly() -> None:
    """The cached prefix is the text. Altering a byte of it defeats the point (invariant 2)."""
    original = transcript()
    marked = apply_cache_control(original, model_slug="anthropic/claude-haiku-4.5")

    for index, message in enumerate(marked):
        content = message.get("content")
        if isinstance(content, list):
            assert content[0]["text"] == original[index]["content"]
        else:
            assert content == original[index].get("content")


def test_marking_is_deterministic() -> None:
    """The same transcript must serialise identically every time, or the prefix never matches."""
    first = apply_cache_control(transcript(), model_slug="anthropic/claude-haiku-4.5")
    second = apply_cache_control(transcript(), model_slug="anthropic/claude-haiku-4.5")

    assert first == second


def test_appending_a_turn_leaves_the_earlier_prefix_intact() -> None:
    """The moving breakpoint is the documented multi-turn pattern, and this is why it is safe:
    `cache_control` is metadata about a block, not content inside it, so the text of every earlier
    message is byte-identical between turns."""
    turn_one = apply_cache_control(transcript(), model_slug="anthropic/claude-haiku-4.5")
    turn_two = apply_cache_control(
        [*transcript(), {"role": "user", "content": "next"}],
        model_slug="anthropic/claude-haiku-4.5",
    )

    def text_of(message: dict[str, Any]) -> Any:
        content = message.get("content")
        return content[0]["text"] if isinstance(content, list) else content

    assert [text_of(m) for m in turn_one] == [text_of(m) for m in turn_two[:-1]]


# ====================================================================== through the gateway


def test_the_gateway_sends_breakpoints_for_an_explicit_vendor() -> None:
    """The wiring, not just the helper — the first version of this fix worked in isolation and was
    never reached, because nothing called it."""
    from chessmark.agents.llm import LlmGateway

    request = LlmGateway().build_request(model="anthropic/claude-haiku-4.5", messages=transcript())

    assert marks(request["messages"]) == [0, 4]


def test_the_gateway_leaves_an_implicit_vendor_untouched() -> None:
    from chessmark.agents.llm import LlmGateway

    request = LlmGateway().build_request(model="deepseek/deepseek-v4-flash", messages=transcript())

    assert marks(request["messages"]) == []


def test_the_stored_transcript_never_carries_a_breakpoint() -> None:
    """`build_request` marks a copy. If it marked in place, the redacted request we persist would
    differ from the transcript rows and LOG-01's audit trail would quietly diverge."""
    from chessmark.agents.llm import LlmGateway

    messages = transcript()
    LlmGateway().build_request(model="anthropic/claude-haiku-4.5", messages=messages)

    assert marks(messages) == []

"""A provider's failure is not the model's failure (ADR-0015).

`deepseek-v4-pro` lost two games to `error_forfeit` for "replying without calling a tool". It had
called tools — StreamLake delivered them as prose instead of parsing them. Through Baidu and
DeepInfra, on identical weights at identical precision, it did not fail once in 40 calls.

Had those games reached a leaderboard they would have read as "this model cannot call tools
reliably": a claim about a model, manufactured entirely by its host. So this case is classified with
provider outages — the game is abandoned, and nobody is forfeited.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from chessmark.agents.mangled import mangled_tool_call
from chessmark.agents.types import Completion, CostSource, TokenUsage, ToolInvocation

# Verbatim from the game that exposed this, StreamLake serving deepseek-v4-pro. The delimiter is
# U+FF5C FULLWIDTH VERTICAL LINE, escaped rather than written literally so nothing can quietly
# normalise it into an ASCII pipe and make this test pass against the wrong bytes.
BAR = "\uff5c"
LEAKED = (
    f" response\n\n<{BAR}DSML{BAR}tool_calls>\n"
    f'<{BAR}DSML{BAR}invoke name="get_board">\n\n</{BAR}DSML{BAR}invoke>\n'
    f'<{BAR}DSML{BAR}invoke name="get_move_history">\n</{BAR}DSML{BAR}tool_calls>'
)


def completion(
    *,
    reasoning: str | None = None,
    content: str | None = None,
    tool_calls: list[ToolInvocation] | None = None,
) -> Completion:
    return Completion(
        model="deepseek/deepseek-v4-pro",
        provider="StreamLake",
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt=10, completion=10),
        cost_usd=Decimal(0),
        cost_source=CostSource.COMPUTED,
        latency_ms=1,
        finish_reason="stop",
        request={},
        response={},
    )


# ====================================================================== the real case


def test_the_leaked_markup_is_recognised() -> None:
    """The exact text that forfeited two games."""
    assert mangled_tool_call(completion(reasoning=LEAKED))


def test_markup_in_content_counts_too() -> None:
    """Where it lands depends on the provider's parser, which is the thing that is broken."""
    assert mangled_tool_call(completion(content=LEAKED))


@pytest.mark.parametrize(
    "markup",
    [
        '<tool_call>{"name": "get_board"}</tool_call>',
        "<function_calls><invoke name='get_board'>",
        "<|python_tag|>get_board()",
    ],
)
def test_other_providers_leak_syntaxes_are_recognised(markup: str) -> None:
    """Qwen, Anthropic and Llama each have their own, and each can leak the same way."""
    assert mangled_tool_call(completion(reasoning=markup))


# ====================================================================== not this


def test_a_real_tool_call_is_never_mangled() -> None:
    """Both halves are required. Markup alongside working tool calls is just a chatty model."""
    call = ToolInvocation(id="1", name="make_move", arguments={"move": "e4"}, raw_arguments="{}")

    assert not mangled_tool_call(completion(reasoning=LEAKED, tool_calls=[call]))


def test_talking_about_a_tool_is_not_calling_one() -> None:
    """The detector must stay narrow. Laundering a genuine refusal into an abandoned game is the
    opposite mistake and a more flattering one, which is reason to be stricter, not looser."""
    assert not mangled_tool_call(
        completion(reasoning="I should call get_board to see the position, then make_move.")
    )
    assert not mangled_tool_call(
        completion(content="Let me use the get_legal_moves tool before deciding.")
    )


def test_an_empty_response_is_not_mangled() -> None:
    """A model that said nothing at all refused to act — that is AGENT-05's case, not this one."""
    assert not mangled_tool_call(completion())
    assert not mangled_tool_call(completion(reasoning="", content=""))


def test_ordinary_chess_prose_is_not_mangled() -> None:
    assert not mangled_tool_call(
        completion(reasoning="The Sicilian is sharp. I'll play c5 and fight for the centre.")
    )

"""Detecting an endpoint that mangled a tool call (ADR-0015).

A model that emits DeepSeek's DSML invoke markup in its reasoning and sends no structured tool
calls did not refuse to act. It acted, and its host failed to parse it. Forfeiting the model for
that publishes a claim about the model produced entirely by the endpoint — which is exactly what
happened: `deepseek-v4-pro` lost two games this way through StreamLake, and lost none at all
through Baidu or DeepInfra on the same weights at the same precision.

Chessmark already separates "played badly" from "we failed to operate" — a provider outage
abandons a game rather than forfeiting anybody. This adds "was failed by its host" to that second
group.

**Deliberately narrow.** Only markup that is unambiguously a tool-call attempt counts. A model
merely *discussing* calling a tool is not this, and treating it as such would launder a genuine
refusal into an abandoned game — the opposite mistake, and a more flattering one, which is reason
to be stricter rather than looser.
"""

from __future__ import annotations

import re

from chessmark.agents.types import Completion

#: Tool-call syntaxes that leak as text when an endpoint fails to parse them. Anchored on the
#: framing tokens rather than on tool names, so a model writing "I will call get_board" in prose is
#: not caught.
MANGLED_MARKUP = re.compile(
    r"""(
        # DeepSeek DSML, seen live via StreamLake. The delimiter is U+FF5C FULLWIDTH VERTICAL
        # LINE, not an ASCII pipe — escaped rather than written literally so it cannot be
        # "tidied" into the wrong character by an editor or a linter.
        <\uff5c[A-Z]+\uff5c(?:tool_calls|invoke)>
      | <\|[a-z_]+\|>\s*(?:tool_calls|invoke) # the ASCII-pipe variant of the same
      | <tool_call>                            # Qwen / Hermes style
      | <function_calls?>                      # Anthropic-style XML
      | <\|python_tag\|>                       # Llama tool syntax
    )""",
    re.VERBOSE | re.IGNORECASE,
)


class ProviderMangledError(Exception):
    """An endpoint returned a tool call it had failed to parse.

    Raised rather than returned so it travels the same path as a provider failure: the worker rolls
    the turn back and, after retries, abandons the game. Nobody is forfeited.
    """

    def __init__(self, model: str, completion: Completion) -> None:
        super().__init__(
            f"{model} emitted tool-call markup its endpoint did not parse "
            f"(provider={completion.provider})"
        )
        self.model = model
        self.provider = completion.provider


def mangled_tool_call(completion: Completion) -> bool:
    """True when the model tried to call a tool and the endpoint delivered it as prose.

    Requires *both* halves: markup present, and no structured tool calls. A response carrying real
    tool calls is fine no matter what its reasoning says.
    """
    if completion.tool_calls:
        return False

    for text in (completion.reasoning, completion.content):
        if text and MANGLED_MARKUP.search(text):
            return True
    return False

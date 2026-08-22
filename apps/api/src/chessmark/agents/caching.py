"""Prompt-cache breakpoints for providers that do not cache on their own.

ADR-0003 builds a byte-stable append-only transcript so the whole game history can be re-sent every
turn cheaply. That reasoning quietly assumed caching is automatic — which is true for OpenAI,
DeepSeek, Moonshot, Grok, Groq and Z.AI, and was true of every model Chessmark had played until a
Claude game cost twelve times what the same game cost its opponent.

**Anthropic and Alibaba cache only what you explicitly mark**, with a `cache_control` breakpoint on
a content block. Without one they cache nothing, and the transcript's O(n²) growth is paid in full
on every turn — exactly the cost ADR-0003 exists to avoid.

Google is a third case: Gemini 2.5+ caches *implicitly* above a minimum prefix size, which is why
its observed hit rate climbs from 24% on a short game to 77% on a long one. Explicit breakpoints
work there too, and cover the early plies implicit caching misses.

**Two breakpoints, not one.** Anthropic allows four. One sits at the end of the system prompt,
which never changes for the life of a game; one sits on the last message of the history. The second
moves forward each turn, and that is the documented pattern rather than a violation of invariant 2:
`cache_control` is metadata about a block, not content inside it, so moving it does not alter the
bytes that make up the cached prefix.
"""

from __future__ import annotations

from typing import Any

#: Vendors whose caching is opt-in. Keyed by the part of an OpenRouter slug before the slash.
#:
#: Everyone absent from this set caches implicitly, and sending them content blocks they did not
#: ask for is a needless difference in request shape — so we do not.
EXPLICIT_CACHE_VENDORS: frozenset[str] = frozenset({"anthropic", "qwen", "google"})

#: The marker itself. `ephemeral` is the only type both Anthropic and Alibaba accept, and the
#: default 5-minute TTL comfortably outlives the gap between two turns of the same game.
CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}


def vendor_of(model_slug: str) -> str:
    return model_slug.split("/", 1)[0].lstrip("~").lower()


def needs_explicit_cache(model_slug: str) -> bool:
    return vendor_of(model_slug) in EXPLICIT_CACHE_VENDORS


def _mark(message: dict[str, Any]) -> dict[str, Any]:
    """Copy a message with its text content converted to a marked content block.

    Returns the message untouched when there is no text to mark — an assistant turn that is nothing
    but tool calls has no block to attach a breakpoint to, and inventing an empty one would change
    the prefix for no benefit.
    """
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return message

    marked = dict(message)
    marked["content"] = [{"type": "text", "text": content, "cache_control": dict(CACHE_CONTROL)}]
    return marked


def _last_markable(messages: list[dict[str, Any]], *, skip: int) -> int | None:
    """Index of the last message carrying text, ignoring the first `skip` of them."""
    for index in range(len(messages) - 1, skip - 1, -1):
        content = messages[index].get("content")
        if isinstance(content, str) and content:
            return index
    return None


def apply_cache_control(messages: list[dict[str, Any]], *, model_slug: str) -> list[dict[str, Any]]:
    """Add cache breakpoints if this model's vendor needs them, otherwise return the list as-is.

    Never mutates the input: the caller stores and logs the same list, and a breakpoint appearing in
    a stored transcript would misrepresent what was sent as what was recorded.
    """
    if not needs_explicit_cache(model_slug) or not messages:
        return messages

    marked = list(messages)

    # The system prompt is a fixed, versioned block for the whole game (invariant 2), which makes
    # it the one thing guaranteed worth caching from ply one.
    if marked[0].get("role") == "system":
        marked[0] = _mark(marked[0])
        tail_start = 1
    else:
        tail_start = 0

    # The second breakpoint rides the end of the history, so each turn extends the cached prefix
    # rather than starting a new one.
    last = _last_markable(marked, skip=tail_start)
    if last is not None and last != 0:
        marked[last] = _mark(marked[last])

    return marked

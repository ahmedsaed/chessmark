"""Normalise provider responses into one shape.

Providers disagree about almost everything that matters here — where reasoning lives, what a
cached token is called, whether reasoning tokens are counted separately. LiteLLM smooths some of
it, but not all, and the gaps are exactly the fields the benchmark reports.

Everything in this module is a **pure function over plain dicts**. That is deliberate: it means
the whole normalisation layer is tested against recorded JSON fixtures with no network, no
provider SDK, and no cost.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from chessmark.agents.types import ParsedResponse, TokenUsage, ToolInvocation, parse_tool_arguments

#: Where different providers put the reasoning trace, in priority order.
REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")

#: Cached-prompt-token field names. OpenAI nests it under `prompt_tokens_details`; Anthropic
#: reports `cache_read_input_tokens` at the top level; OpenRouter passes through either.
CACHED_TOKEN_KEYS = ("cached_tokens", "cache_read_input_tokens", "cache_read_tokens")


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _first_str(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value
        # Anthropic-style thinking blocks arrive as a list of typed parts.
        if isinstance(value, list):
            parts = [
                block.get("thinking") or block.get("text")
                for block in value
                if isinstance(block, dict)
            ]
            joined = "\n".join(part for part in parts if isinstance(part, str) and part.strip())
            if joined:
                return joined
    return None


def extract_reasoning(message: dict[str, Any]) -> str | None:
    """Pull the reasoning trace out of a message, wherever the provider put it (AGENT-07)."""
    direct = _first_str(message, REASONING_KEYS)
    if direct:
        return direct

    for container_key in ("provider_specific_fields", "thinking_blocks"):
        container = message.get(container_key)
        if isinstance(container, dict):
            nested = _first_str(container, REASONING_KEYS)
            if nested:
                return nested
        elif isinstance(container, list):
            nested = _first_str({container_key: container}, (container_key,))
            if nested:
                return nested

    return None


def extract_usage(payload: dict[str, Any]) -> TokenUsage:
    """Read token counts, including the cached and reasoning breakdowns (LOG-02, NFR-06)."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()

    prompt = _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _as_int(usage.get("completion_tokens") or usage.get("output_tokens"))

    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")

    cached = 0
    for key in CACHED_TOKEN_KEYS:
        if isinstance(prompt_details, dict) and key in prompt_details:
            cached = _as_int(prompt_details[key])
            break
        if key in usage:
            cached = _as_int(usage[key])
            break

    reasoning = 0
    if isinstance(completion_details, dict):
        reasoning = _as_int(completion_details.get("reasoning_tokens"))
    if not reasoning:
        reasoning = _as_int(usage.get("reasoning_tokens"))

    # Some providers report cached tokens *in addition to* prompt tokens rather than as a subset.
    # Clamping keeps `uncached_prompt` from going negative and the hit rate from exceeding 1.
    cached = min(cached, prompt) if prompt else cached

    return TokenUsage(prompt=prompt, completion=completion, reasoning=reasoning, cached=cached)


def extract_provider_cost(payload: dict[str, Any]) -> Decimal | None:
    """OpenRouter's own charge for the call, when it reports one.

    Always preferred over our computed figure: it is what was actually billed, and invariant 4
    says cost is measured rather than estimated.
    """
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("cost", "total_cost"):
            cost = _as_decimal(usage.get(key))
            if cost is not None:
                return cost
    return _as_decimal(payload.get("cost"))


def _provider_of(payload: dict[str, Any]) -> str | None:
    """Which endpoint served the call.

    OpenRouter puts the provider name at the top level of the response. It does *not* report the
    quantization, so this name is the join key into `model_endpoints`.
    """
    provider = payload.get("provider")
    return str(provider) if isinstance(provider, str) and provider.strip() else None


def extract_tool_calls(message: dict[str, Any]) -> list[ToolInvocation]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    invocations: list[ToolInvocation] = []
    for index, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue

        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            # A few providers hand back an already-parsed object.
            raw_arguments = "" if raw_arguments is None else str(raw_arguments)

        arguments, error = parse_tool_arguments(raw_arguments)
        invocations.append(
            ToolInvocation(
                id=str(call.get("id") or f"call_{index}"),
                name=str(function.get("name") or ""),
                arguments=arguments,
                raw_arguments=raw_arguments,
                parse_error=error,
            )
        )
    return invocations


def normalise_response(payload: dict[str, Any]) -> ParsedResponse:
    """Turn any supported provider response into a `ParsedResponse`.

    Tolerant by design: a missing choice, a null message, or an unexpected shape yields an empty
    result rather than an exception. The turn loop decides what to do about a model that said
    nothing — that is a benchmark observation, not a crash.
    """
    choices = payload.get("choices")
    choice: dict[str, Any] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]

    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}

    content = message.get("content")
    if isinstance(content, list):
        # Anthropic-style content blocks.
        content = "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    if not isinstance(content, str) or not content.strip():
        content = None

    return ParsedResponse(
        content=content,
        reasoning=extract_reasoning(message),
        tool_calls=extract_tool_calls(message),
        usage=extract_usage(payload),
        finish_reason=choice.get("finish_reason") or choice.get("stop_reason"),
        model=payload.get("model"),
        provider=_provider_of(payload),
        provider_cost_usd=extract_provider_cost(payload),
    )

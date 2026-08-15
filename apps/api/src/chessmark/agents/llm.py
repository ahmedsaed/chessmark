"""The LLM gateway: one function that reaches any OpenRouter model and records everything.

Two design choices carry most of the weight:

* **The provider call is injectable.** `LlmGateway` takes a `completion_fn`; the default reaches
  LiteLLM, and tests pass a function that replays a recorded fixture. The whole gateway is
  therefore testable with no network and no spend.
* **Retries are classified, not blanket.** A 500 is worth retrying; a 400 never is, and retrying
  it just burns the budget three times over. Provider failures are also kept strictly separate
  from illegal-move retries (ADR-0002) — a flaky network must never count against a model's
  benchmark score.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from chessmark.agents.normalise import normalise_response
from chessmark.agents.pricing import PricingTable, compute_cost
from chessmark.agents.redaction import redact
from chessmark.agents.types import Completion, CostSource, LlmError

CompletionFn = Callable[..., Awaitable[Any]]
SleepFn = Callable[[float], Awaitable[None]]

#: Status codes worth trying again. 408 timeout, 409 conflict, 429 rate limit, and 5xx.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

#: Exception class names that mean "transient", matched by name so this module never has to
#: import LiteLLM's exception hierarchy at module scope.
RETRYABLE_EXCEPTION_NAMES = frozenset(
    {
        "APIConnectionError",
        "APIError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailableError",
        "Timeout",
        "APITimeoutError",
        "ConnectionError",
        "TimeoutError",
    }
)

#: Never retried: the same request will fail the same way, and each attempt costs money.
FATAL_EXCEPTION_NAMES = frozenset(
    {
        "AuthenticationError",
        "BadRequestError",
        "ContentPolicyViolationError",
        "ContextWindowExceededError",
        "InvalidRequestError",
        "NotFoundError",
        "PermissionDeniedError",
        "UnprocessableEntityError",
        "UnsupportedParamsError",
    }
)


def _status_code(error: BaseException) -> int | None:
    for attribute in ("status_code", "http_status", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable(error: BaseException) -> bool:
    """Classify a provider failure.

    Errs toward *not* retrying: an unrecognised error is treated as fatal, because a retry loop
    on a permanent failure is a way to spend money three times for nothing.
    """
    name = type(error).__name__
    if name in FATAL_EXCEPTION_NAMES:
        return False

    status = _status_code(error)
    if status is not None:
        return status in RETRYABLE_STATUS

    return name in RETRYABLE_EXCEPTION_NAMES


async def _default_completion(**kwargs: Any) -> Any:
    # Imported lazily: LiteLLM is slow to import and the API process should not pay for it at
    # startup, nor should tests that never make a call.
    import litellm

    return await litellm.acompletion(**kwargs)


def _to_dict(response: Any) -> dict[str, Any]:
    """Coerce whatever the provider layer returned into a plain dict.

    Recorded fixtures are already dicts; LiteLLM returns a pydantic model. Normalising here keeps
    everything downstream working on plain data.
    """
    if isinstance(response, dict):
        return response
    for method in ("model_dump", "dict", "json"):
        converter = getattr(response, method, None)
        if callable(converter):
            result = converter()
            if isinstance(result, dict):
                return result
    return {}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with jitter, so concurrent workers do not retry in lockstep."""
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return float(delay + random.uniform(0, self.jitter * delay))


class LlmGateway:
    """Makes provider calls and returns everything needed to persist an `llm_calls` row."""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        pricing: PricingTable | None = None,
        retry: RetryPolicy | None = None,
        completion_fn: CompletionFn | None = None,
        sleep_fn: SleepFn | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.pricing = pricing or PricingTable()
        self.retry = retry or RetryPolicy()
        self._complete = completion_fn or _default_completion
        self._sleep = sleep_fn or asyncio.sleep
        self.timeout = timeout

    def build_request(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Every OpenRouter id already contains a slash (`nvidia/nemotron-nano-9b-v2:free`), so the
        # prefix must be tested for explicitly. Without it LiteLLM reads the vendor half as its
        # own provider name and routes to the wrong place — or, worse, to the right place by
        # accident for the one vendor whose name it recognises.
        request: dict[str, Any] = {
            "model": model if model.startswith("openrouter/") else f"openrouter/{model}",
            "messages": messages,
            "timeout": self.timeout,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = tool_choice or "auto"
        if temperature is not None:
            request["temperature"] = temperature
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        # Ask OpenRouter to report what it actually charged, so cost comes from the provider
        # rather than from our own multiplication (invariant 4).
        request["extra_body"] = {"usage": {"include": True}}

        if extra:
            request.update(extra)
        return request

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Completion:
        """Make one logical call, retrying transient failures.

        Raises `LlmError` when every attempt fails. Never raises for a *model* failure — an empty
        response or a malformed tool call comes back as data, because those are the things the
        benchmark exists to count.
        """
        request = self.build_request(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
        )
        redacted_request = redact(request)

        call_kwargs = dict(request)
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["api_base"] = self.base_url

        last_error: BaseException | None = None

        for attempt in range(1, self.retry.max_attempts + 1):
            started = time.perf_counter()
            try:
                raw = await self._complete(**call_kwargs)
            except Exception as error:
                last_error = error
                if not is_retryable(error) or attempt == self.retry.max_attempts:
                    raise LlmError(
                        message=str(error),
                        status_code=_status_code(error),
                        retryable=is_retryable(error),
                        attempts=attempt,
                        request=redacted_request,
                    ) from error

                await self._sleep(self.retry.delay_for(attempt))
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)
            return self._build_completion(
                model=model,
                raw=raw,
                request=redacted_request,
                latency_ms=latency_ms,
                attempts=attempt,
            )

        # Unreachable: the loop either returns or raises.
        raise LlmError(  # pragma: no cover
            message=str(last_error),
            attempts=self.retry.max_attempts,
            request=redacted_request,
        )

    def _build_completion(
        self,
        *,
        model: str,
        raw: Any,
        request: dict[str, Any],
        latency_ms: int,
        attempts: int,
    ) -> Completion:
        payload = _to_dict(raw)
        parsed = normalise_response(payload)

        breakdown = compute_cost(
            parsed.usage,
            self.pricing.get(model),
            provider_cost_usd=parsed.provider_cost_usd,
        )

        return Completion(
            model=parsed.model or model,
            content=parsed.content,
            reasoning=parsed.reasoning,
            tool_calls=parsed.tool_calls,
            usage=parsed.usage,
            cost_usd=breakdown.total_usd,
            cost_source=breakdown.source,
            latency_ms=latency_ms,
            finish_reason=parsed.finish_reason,
            request=request,
            response=redact(payload),
            attempts=attempts,
        )


__all__ = [
    "CompletionFn",
    "CostSource",
    "Decimal",
    "LlmError",
    "LlmGateway",
    "RetryPolicy",
    "is_retryable",
]

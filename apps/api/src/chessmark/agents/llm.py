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
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from chessmark.agents.caching import apply_cache_control
from chessmark.agents.normalise import normalise_response
from chessmark.agents.pricing import PricingTable, compute_cost
from chessmark.agents.redaction import redact
from chessmark.agents.routing import ProviderRouting
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


class ProviderDeadlineError(TimeoutError):
    """Our own deadline expired while a call was still generating.

    Distinct from a network timeout, and deliberately *not* retryable. A call that ran to the
    deadline was producing tokens the whole time — it was not stalled — so trying again just
    spends the same wall clock to reach the same place.
    """


log = logging.getLogger(__name__)


#: Never retried: the same request will fail the same way, and each attempt costs money.
FATAL_EXCEPTION_NAMES = frozenset(
    {
        "ProviderDeadlineError",
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


def is_rate_limit(error: BaseException) -> bool:
    """Whether a provider is asking us to slow down, rather than failing.

    Worth telling apart, because it is the one failure where the provider says *when* to come
    back and where waiting is the correct response rather than a hedge.
    """
    if _status_code(error) == 429:
        return True
    return "RateLimit" in type(error).__name__


#: `"retry_after_seconds":5` and `"Retry-After":"5"`, as they appear in a provider's error body.
_RETRY_AFTER = re.compile(r'"?[Rr]etry[-_][Aa]fter(?:_seconds)?"?\s*[:=]\s*"?(\d+(?:\.\d+)?)"?')


def retry_after_seconds(error: BaseException) -> float | None:
    """How long the provider asked us to wait, if it said.

    OpenRouter puts it in three places at once — a `Retry-After` header, and both
    `retry_after_seconds` and a nested `headers` object in the error body — so this looks at the
    response headers first and falls back to reading the message, which is where it actually
    arrives once LiteLLM has wrapped the error.
    """
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in ("retry-after", "Retry-After"):
            try:
                value = headers.get(name)
            except (AttributeError, TypeError):  # pragma: no cover - exotic header objects
                value = None
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass

    match = _RETRY_AFTER.search(str(error))
    return float(match.group(1)) if match else None


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

    #: Rate limits get their own budget, because they are a different kind of failure. A 500 is
    #: the provider being broken and retrying hard is reasonable; a 429 is the provider telling us
    #: when to come back, and the right answer is to wait that long.
    #:
    #: The free tier makes this concrete: its models come from a shared pool that rate-limits
    #: constantly, and backing off a maximum of eight seconds meant ~20 doomed requests in a
    #: minute and then abandoning the game. A tournament has no deadline; patience is free.
    rate_limit_attempts: int = 8
    rate_limit_max_delay: float = 300.0

    def attempts_for(self, error: BaseException | None = None) -> int:
        """How many tries this kind of failure gets."""
        if error is not None and is_rate_limit(error):
            return max(self.max_attempts, self.rate_limit_attempts)
        return self.max_attempts

    def delay_for(self, attempt: int, error: BaseException | None = None) -> float:
        """How long to wait before the next attempt.

        Exponential backoff with jitter, so concurrent workers do not retry in lockstep — except
        when the provider has told us how long to wait, which is better information than any
        formula and is honoured up to `rate_limit_max_delay`.
        """
        if error is not None and is_rate_limit(error):
            asked = retry_after_seconds(error)
            base = asked if asked is not None else self.base_delay * (2 ** (attempt - 1))
            delay = min(max(base, self.base_delay), self.rate_limit_max_delay)
        else:
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
        routing: ProviderRouting | None = None,
        retry: RetryPolicy | None = None,
        completion_fn: CompletionFn | None = None,
        sleep_fn: SleepFn | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.pricing = pricing or PricingTable()
        self.routing = routing
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
            # Cache breakpoints are added here rather than in the transcript, so the stored history
            # stays a record of what we *built* and the request carries what we *sent*. Anthropic
            # and Alibaba cache nothing without them; see `agents/caching.py`.
            "messages": apply_cache_control(messages, model_slug=model),
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
        extra_body: dict[str, Any] = {"usage": {"include": True}}

        # Constrain which endpoint may serve this. Without it the router is free to pick a 4-bit
        # provider, and the model on the leaderboard is not the model that played.
        if self.routing is not None:
            extra_body["provider"] = self.routing.to_request()

        request["extra_body"] = extra_body

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
        deadline_seconds: float | None = None,
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

        deadline = deadline_seconds if deadline_seconds is not None else self.timeout
        last_error: BaseException | None = None

        # The budget depends on what goes wrong, so it is recomputed as failures arrive rather
        # than fixed before the first attempt.
        allowed = self.retry.max_attempts
        attempt = 0

        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                # Enforced here rather than trusted to the provider library. `timeout` is passed
                # in the request too, but it was observed not to bind: a single call ran for 1,093
                # seconds against a 180-second setting, generating the whole time. A deadline that
                # only the callee honours is not a deadline.
                raw = await asyncio.wait_for(self._complete(**call_kwargs), timeout=deadline)
            except TimeoutError as error:
                raise LlmError(
                    message=f"provider call exceeded {deadline:.0f}s",
                    retryable=False,
                    attempts=attempt,
                    request=redacted_request,
                ) from error
            except Exception as error:
                last_error = error
                allowed = self.retry.attempts_for(error)
                if not is_retryable(error) or attempt >= allowed:
                    raise LlmError(
                        message=str(error),
                        status_code=_status_code(error),
                        retryable=is_retryable(error),
                        attempts=attempt,
                        request=redacted_request,
                    ) from error

                wait = self.retry.delay_for(attempt, error)
                if is_rate_limit(error):
                    log.info(
                        "rate limited by the provider; waiting %.0fs before attempt %d of %d",
                        wait,
                        attempt + 1,
                        allowed,
                    )
                await self._sleep(wait)
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
                attempts=allowed,
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
            provider=parsed.provider,
            model=parsed.model or model,
            content=parsed.content,
            reasoning=parsed.reasoning,
            reasoning_details=parsed.reasoning_details,
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

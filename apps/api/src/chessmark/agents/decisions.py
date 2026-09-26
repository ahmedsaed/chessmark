"""The decision gateway: one call to OpenRouter's Decisions API, recorded whole (ADR-0049).

A decision model is not a chat model and does not go through LiteLLM. It answers
`POST /api/alpha/decisions` with typed answers and a probability per option, and nothing else — no
text, no tool calls, no transcript. What it shares with `LlmGateway` is everything that is about
*the provider* rather than about the model: the retry policy, the classification of a refusal
(rate limit, account, gated, unhealthy, rejected), pinned routing, attribution, and the rule that
cost comes from what the provider reported (invariant 4). Those are imported rather than restated,
so a refusal means the same thing here as there and the worker handles both with one set of rules.

The HTTP call is injectable, exactly as `LlmGateway`'s is, so every path is testable with no network
and no spend.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from chessmark.agents.attribution import attribution_headers
from chessmark.agents.llm import (
    AttemptFn,
    RetryPolicy,
    SleepFn,
    SuccessFn,
    is_retryable,
    is_unavailable,
    rate_limit_from,
    rejects_the_request,
)
from chessmark.agents.pricing import PricingTable, compute_cost
from chessmark.agents.redaction import redact
from chessmark.agents.routing import ProviderRouting
from chessmark.agents.types import CostSource, LlmError, RateLimit, TokenUsage

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

#: Sends one request body and returns the parsed response, raising `DecisionHttpError` on a non-2xx.
DecideFn = Callable[..., Awaitable[dict[str, Any]]]

#: A decision takes under a second where a chat turn can take ten minutes, so the deadline is
#: correspondingly shorter. It is still generous — an endpoint that has not answered in two minutes
#: is not serving us, and that is the same conclusion a 429 reaches (ADR-0017).
DEFAULT_TIMEOUT_SECONDS = 120.0


class DecisionHttpError(Exception):
    """A non-2xx from the Decisions API, shaped so `agents/llm.py`'s classifiers can read it.

    They look for a `status_code`, for response headers (`Retry-After`, `X-RateLimit-Reset`) and
    for phrases in the body (`limit_source`, `provider_name`, the free-tier cap). This carries all
    three, with the body as the message, so a 429 from here is classified by the same code that
    classifies one from the chat endpoint — rather than by a second copy that would drift from it.
    """

    def __init__(self, status_code: int, body: str, response: httpx.Response | None = None):
        super().__init__(body)
        self.status_code = status_code
        self.response = response


class MalformedDecisionError(Exception):
    """The provider answered 200 with something that is not an answer to what we asked.

    **The endpoint's fault, not the model's**, in the sense invariant 11 cares about: a missing
    question key or a choice outside the options we offered is not a move the model chose, and
    recording it as one — or forfeiting the model for it — would publish a claim the endpoint
    manufactured. The turn fails and the worker decides, as for any provider failure.
    """


async def _http_decide(
    body: dict[str, Any],
    *,
    api_key: str,
    headers: dict[str, str],
    http_timeout_seconds: float,
    url: str = DECISIONS_URL,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=http_timeout_seconds) as client:
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {api_key}", **headers},
            )
    except httpx.TransportError as error:
        # A dropped connection is transient, and `is_retryable` knows that by the builtin's name.
        # httpx's own names are not in its list, and teaching it a second library's hierarchy to
        # say the same thing would be the worse change.
        raise ConnectionError(f"{type(error).__name__}: {error}") from error
    if response.status_code >= 400:
        raise DecisionHttpError(response.status_code, response.text, response)
    payload: dict[str, Any] = response.json()
    return payload


@dataclass(frozen=True, slots=True)
class Decision:
    """One answered request, with everything an `llm_calls` row needs."""

    #: The exact build that answered (`typesafe/jev-1.13-20260917`), as the response names it.
    model: str
    provider: str | None
    answers: dict[str, Any]
    usage: TokenUsage
    cost_usd: Decimal
    cost_source: CostSource
    latency_ms: int
    request: dict[str, Any]
    """Verbatim, redacted (LOG-01)."""
    response: dict[str, Any]
    """Verbatim, redacted."""
    attempts: int = 1

    def _answer(self, key: str, kind: str) -> dict[str, Any]:
        answer = self.answers.get(key)
        # Every answer carries its `type`; a missing key or a different type is an error rather
        # than something to default, because a default here would be a move nobody chose.
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise MalformedDecisionError(
                f"expected a {kind} answer for {key!r}, got {json.dumps(answer)[:200]}"
            )
        return answer

    def choice(self, key: str, options: set[str]) -> tuple[str, dict[str, float]]:
        """The option chosen, and the probability of every option, checked against what we asked.

        `options` is the set we offered. An answer naming anything else is refused here, before a
        referee could be handed it — the model can only choose among legal moves because we check
        that it did, not because we trust that it would (invariant 1).
        """
        answer = self._answer(key, "choice")
        chosen = answer.get("choice")
        if chosen not in options:
            raise MalformedDecisionError(f"{key!r} answered {chosen!r}, which was not offered")
        raw = answer.get("probabilities") or {}
        probabilities = {
            str(option): float(value)
            for option, value in raw.items()
            if option in options and isinstance(value, int | float)
        }
        return str(chosen), probabilities

    def noul(self, key: str) -> float:
        """The probability of yes."""
        value = self._answer(key, "noul").get("noul")
        if not isinstance(value, int | float) or not 0 <= value <= 1:
            raise MalformedDecisionError(f"{key!r} answered {value!r}, not a probability")
        return float(value)

    def confidence(self, key: str) -> float | None:
        answer = self.answers.get(key)
        value = answer.get("confidence") if isinstance(answer, dict) else None
        return float(value) if isinstance(value, int | float) else None


class DecisionGateway:
    """Makes Decisions API calls and returns everything needed to persist the call."""

    def __init__(
        self,
        *,
        api_key: str = "",
        pricing: PricingTable | None = None,
        routing: ProviderRouting | None = None,
        retry: RetryPolicy | None = None,
        decide_fn: DecideFn | None = None,
        sleep_fn: SleepFn | None = None,
        on_attempt: AttemptFn | None = None,
        on_success: SuccessFn | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        attribution: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.pricing = pricing or PricingTable()
        #: Set per seat by the worker, as `LlmGateway.routing` is: `only` names a provider, and
        #: providers are model-specific.
        self.routing = routing
        self.retry = retry or RetryPolicy()
        self._decide = decide_fn
        self._sleep = sleep_fn or asyncio.sleep
        self._on_attempt = on_attempt
        #: Public for the same reason `LlmGateway.on_success` is: the worker owns the cooldown and
        #: wires it after the gateway is built.
        self.on_success = on_success
        self.timeout = timeout
        self.attribution = attribution if attribution is not None else attribution_headers()

    def build_request(
        self,
        body: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        request = dict(body)
        # Pinned for the whole game, as a chat seat is (ADR-0015): the model on the leaderboard
        # must be the one that played, served by the endpoint that was chosen.
        if self.routing is not None:
            request["provider"] = self.routing.to_request()
        # Groups the game's calls on OpenRouter's dashboard, as `session_id` does for chat calls.
        if session_id:
            request["session_id"] = session_id
        return request

    async def decide(self, body: dict[str, Any], *, session_id: str | None = None) -> Decision:
        """Make one logical call, retrying transient failures. Raises `LlmError` when all fail.

        `LlmError` rather than a type of its own, deliberately: the turn runner and the worker
        already know what a rate limit, an account refusal and a rejected request each call for,
        and a second exception hierarchy would be a second place to get that wrong.
        """
        request = self.build_request(body, session_id=session_id)
        redacted_request = redact(request)
        model = str(request.get("model") or "")
        pinned = self.routing.only[0] if self.routing and self.routing.only else None

        attempt = 0
        while True:
            attempt += 1
            if self._on_attempt is not None:
                await self._on_attempt(model)
            started = time.perf_counter()
            try:
                raw = await asyncio.wait_for(self._call(request), timeout=self.timeout)
            except TimeoutError as error:
                # Unavailability, paused rather than retried — the reasoning is `LlmGateway`'s.
                raise LlmError(
                    message=f"provider did not answer within {self.timeout:.0f}s",
                    attempts=attempt,
                    request=redacted_request,
                    rate_limit=RateLimit(provider=pinned, timed_out=True),
                ) from error
            except Exception as error:
                allowed = self.retry.attempts_for(error)
                if not is_retryable(error) or attempt >= allowed:
                    raise LlmError(
                        message=str(error),
                        status_code=getattr(error, "status_code", None),
                        retryable=is_retryable(error),
                        attempts=attempt,
                        request=redacted_request,
                        rate_limit=rate_limit_from(error) if is_unavailable(error) else None,
                        request_rejected=rejects_the_request(error),
                    ) from error
                await self._sleep(self.retry.delay_for(attempt, error))
                continue

            decision = self._build(
                model=model,
                raw=raw,
                request=redacted_request,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=attempt,
            )
            if self.on_success is not None:
                with contextlib.suppress(Exception):
                    await self.on_success(model, decision.provider or pinned)
            return decision

    async def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._decide is not None:
            return await self._decide(request)
        # Only with a key, as `LlmGateway` does: attribution is a claim about who is calling.
        headers = dict(self.attribution) if self.api_key and self.attribution else {}
        return await _http_decide(
            request, api_key=self.api_key, headers=headers, http_timeout_seconds=self.timeout
        )

    def _build(
        self,
        *,
        model: str,
        raw: dict[str, Any],
        request: dict[str, Any],
        latency_ms: int,
        attempts: int,
    ) -> Decision:
        usage_raw = raw.get("usage") or {}
        usage = TokenUsage(
            prompt=int(usage_raw.get("input_tokens") or 0),
            completion=int(usage_raw.get("output_tokens") or 0),
        )
        reported: Decimal | None = None
        if usage_raw.get("cost") is not None:
            with contextlib.suppress(InvalidOperation, ValueError):
                # Through `str`, so a float such as 5.1366e-05 keeps the digits it was sent with.
                reported = Decimal(str(usage_raw["cost"]))
        cost = compute_cost(usage, self.pricing.get(model), provider_cost_usd=reported)

        answers = raw.get("answers")
        return Decision(
            model=str(raw.get("model") or model),
            provider=raw.get("provider"),
            answers=answers if isinstance(answers, dict) else {},
            usage=usage,
            cost_usd=cost.total_usd,
            cost_source=cost.source,
            latency_ms=latency_ms,
            request=request,
            response=redact(raw),
            attempts=attempts,
        )


__all__ = [
    "DECISIONS_URL",
    "DecideFn",
    "Decision",
    "DecisionGateway",
    "DecisionHttpError",
    "MalformedDecisionError",
]

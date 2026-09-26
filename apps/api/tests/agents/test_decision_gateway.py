"""The decision gateway: one Decisions API call, classified and recorded (ADR-0049).

The HTTP call is replaced; everything else is real. Bodies are the shapes the live endpoint
returned when it was probed on 2026-09-26, errors included.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from chessmark.agents.decisions import (
    DecisionGateway,
    DecisionHttpError,
    MalformedDecisionError,
)
from chessmark.agents.llm import RetryPolicy
from chessmark.agents.routing import ProviderRouting
from chessmark.agents.types import CostSource, LlmError

ANSWERED = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "move": {
            "type": "choice",
            "choice": "Qxf7",
            "probabilities": {"Qxf7": 0.3, "Qxe5": 0.23, "Bxf7": 0.17},
            "confidence": 0.28,
        },
        "resign": {"type": "noul", "noul": 0.02},
    },
    "usage": {"input_tokens": 1223, "output_tokens": 377, "cost": 5.1366e-05},
    "id": "gen-dec-1790372857-2sCPZCbZ8BsWSUheSEjG",
    "provider": "TypeSafe",
}

BODY = {"model": "typesafe/jev-1.13", "state": {}, "questions": {}}


async def _instant(_: float) -> None:
    return None


def _gateway(*responses: Any, **kwargs: Any) -> tuple[DecisionGateway, list[dict[str, Any]]]:
    """A gateway whose calls answer from `responses` in order; an exception is raised."""
    sent: list[dict[str, Any]] = []
    queue = list(responses)

    async def decide(request: dict[str, Any]) -> dict[str, Any]:
        sent.append(request)
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return dict(item)

    kwargs.setdefault("retry", RetryPolicy(max_attempts=3, rate_limit_attempts=2))
    return DecisionGateway(decide_fn=decide, sleep_fn=_instant, **kwargs), sent


def _refusal(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> Exception:
    response = httpx.Response(status, json=body, headers=headers or {})
    return DecisionHttpError(status, response.text, response)


async def test_an_answer_carries_the_build_the_provider_and_what_it_cost() -> None:
    gateway, _ = _gateway(ANSWERED)
    decision = await gateway.decide(BODY)

    assert decision.model == "typesafe/jev-1.13-20260917"
    assert decision.provider == "TypeSafe"
    assert (decision.usage.prompt, decision.usage.completion) == (1223, 377)
    # The provider's figure, digit for digit (invariant 4) — through `str`, not through a float.
    assert decision.cost_usd == Decimal("0.000051366")
    assert decision.cost_source is CostSource.PROVIDER
    assert decision.choice("move", {"Qxf7", "Qxe5", "Bxf7"}) == (
        "Qxf7",
        {"Qxf7": 0.3, "Qxe5": 0.23, "Bxf7": 0.17},
    )
    assert decision.noul("resign") == 0.02
    assert decision.confidence("move") == 0.28


async def test_the_seat_is_pinned_and_grouped_under_its_game() -> None:
    gateway, sent = _gateway(ANSWERED, routing=ProviderRouting(only=("TypeSafe",)))
    await gateway.decide(BODY, session_id="game-1")
    assert sent[0]["provider"]["only"] == ["TypeSafe"]
    assert sent[0]["session_id"] == "game-1"


async def test_the_record_is_verbatim() -> None:
    gateway, _ = _gateway(ANSWERED)
    decision = await gateway.decide(BODY)
    assert decision.response["answers"] == ANSWERED["answers"]
    assert decision.request["model"] == BODY["model"]


async def test_a_choice_nobody_offered_is_refused_before_anyone_can_play_it() -> None:
    """Invariant 1: the model chooses among legal moves because we check that it did."""
    gateway, _ = _gateway(ANSWERED)
    decision = await gateway.decide(BODY)
    with pytest.raises(MalformedDecisionError):
        decision.choice("move", {"e4", "d4"})


@pytest.mark.parametrize(
    "answer",
    [None, {"type": "noul", "noul": 0.5}, {"type": "choice"}],
    ids=["missing", "wrong type", "no choice"],
)
async def test_a_missing_or_mistyped_answer_is_an_error_not_a_default(answer: Any) -> None:
    body = json.loads(json.dumps(ANSWERED))
    if answer is None:
        del body["answers"]["move"]
    else:
        body["answers"]["move"] = answer
    gateway, _ = _gateway(body)
    decision = await gateway.decide(BODY)
    with pytest.raises(MalformedDecisionError):
        decision.choice("move", {"Qxf7"})


async def test_a_probability_outside_zero_to_one_is_not_a_probability() -> None:
    body = json.loads(json.dumps(ANSWERED))
    body["answers"]["resign"]["noul"] = 1.7
    gateway, _ = _gateway(body)
    with pytest.raises(MalformedDecisionError):
        (await gateway.decide(BODY)).noul("resign")


async def test_a_rate_limit_is_retried_a_little_and_then_handed_back_as_one() -> None:
    """The same classification the chat gateway gives a 429, from the same code."""
    limited = _refusal(
        429,
        {"error": {"message": "HTTP 429: TPM limit reached.", "code": 429}},
        {"Retry-After": "7"},
    )
    gateway, sent = _gateway(limited, limited)
    with pytest.raises(LlmError) as caught:
        await gateway.decide(BODY)

    assert len(sent) == 2
    assert caught.value.rate_limit is not None
    assert caught.value.rate_limit.retry_after_seconds == 7.0
    assert not caught.value.request_rejected


async def test_a_rate_limit_that_clears_is_simply_answered() -> None:
    limited = _refusal(429, {"error": {"message": "slow down", "code": 429}})
    gateway, sent = _gateway(limited, ANSWERED)
    decision = await gateway.decide(BODY)
    assert decision.provider == "TypeSafe"
    assert len(sent) == 2


async def test_a_malformed_request_is_rejected_once_and_not_retried() -> None:
    refused = _refusal(
        400, {"error": {"message": "At least one question is required", "code": 400}}
    )
    gateway, sent = _gateway(refused)
    with pytest.raises(LlmError) as caught:
        await gateway.decide(BODY)
    assert len(sent) == 1
    assert caught.value.request_rejected


async def test_a_pinned_provider_that_is_not_serving_pauses_rather_than_rejects() -> None:
    """The probe's own 404: "No allowed providers are available for the selected model"."""
    gone = _refusal(
        404,
        {"error": {"message": "No allowed providers are available", "code": 404}},
    )
    gateway, _ = _gateway(gone)
    with pytest.raises(LlmError) as caught:
        await gateway.decide(BODY)
    assert caught.value.rate_limit is not None
    assert caught.value.rate_limit.status_code == 404
    assert not caught.value.request_rejected


async def test_an_empty_account_is_an_account_problem() -> None:
    gateway, _ = _gateway(
        _refusal(402, {"error": {"message": "Insufficient credits", "code": 402}})
    )
    with pytest.raises(LlmError) as caught:
        await gateway.decide(BODY)
    assert caught.value.rate_limit is not None and caught.value.rate_limit.account


async def test_a_dropped_connection_is_retried() -> None:
    gateway, sent = _gateway(ConnectionError("ConnectError: reset"), ANSWERED)
    await gateway.decide(BODY)
    assert len(sent) == 2


async def test_a_call_that_never_answers_pauses_the_game() -> None:
    import asyncio

    async def hang(_: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    gateway = DecisionGateway(decide_fn=hang, timeout=0.01)
    with pytest.raises(LlmError) as caught:
        await gateway.decide(BODY)
    assert caught.value.rate_limit is not None and caught.value.rate_limit.timed_out


async def test_an_answered_call_clears_the_endpoints_cooldown() -> None:
    served: list[tuple[str, str | None]] = []

    async def on_success(model: str, provider: str | None) -> None:
        served.append((model, provider))

    gateway, _ = _gateway(ANSWERED, on_success=on_success)
    await gateway.decide(BODY)
    assert served == [("typesafe/jev-1.13", "TypeSafe")]


async def test_the_real_transport_turns_an_error_status_into_a_classifiable_error() -> None:
    """The default HTTP path, against a mock transport rather than the network."""
    from chessmark.agents import decisions

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(429, json={"error": {"message": "limited", "code": 429}})

    real = httpx.AsyncClient

    class Mocked(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    decisions.httpx.AsyncClient = Mocked  # type: ignore[misc]
    try:
        with pytest.raises(DecisionHttpError) as caught:
            await decisions._http_decide(
                BODY, api_key="sk-test", headers={}, http_timeout_seconds=5
            )
    finally:
        decisions.httpx.AsyncClient = real  # type: ignore[misc]
    assert caught.value.status_code == 429

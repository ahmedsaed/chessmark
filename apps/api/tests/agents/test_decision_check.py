"""A decision model is asked once whether it can answer a turn, before it is offered (ADR-0051)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import decision_check
from chessmark.agents.decision_check import check_decision_models
from chessmark.agents.decision_request import ACTION_QUESTION, DECISION_VERSION, MOVE_QUESTION
from chessmark.agents.decisions import DecisionGateway, DecisionHttpError
from chessmark.agents.llm import RetryPolicy
from chessmark.agents.registry import ineligible_reasons, playable_models
from chessmark.agents.scripted_decisions import deciding
from chessmark.db.enums import ModelRuntime
from chessmark.db.models import ModelRegistry

pytestmark = pytest.mark.integration

#: What Respan's host answered on 2026-09-26, verbatim.
RESPAN_400 = (
    "Respan only accepts noul questions whose instructions and criteria are plain strings "
    '(question "move")'
)


async def _instant(_: float) -> None:
    return None


def _refusing(status: int, message: str) -> Any:
    calls: list[dict[str, Any]] = []

    async def decide(request: dict[str, Any]) -> dict[str, Any]:
        calls.append(request)
        response = httpx.Response(status, json={"error": {"message": message, "code": status}})
        raise DecisionHttpError(status, response.text, response)

    decide.calls = calls  # type: ignore[attr-defined]
    return decide


def _gateway(decide: Any) -> DecisionGateway:
    return DecisionGateway(
        decide_fn=decide,
        sleep_fn=_instant,
        retry=RetryPolicy(max_attempts=1, rate_limit_attempts=1),
    )


async def _model(db: AsyncSession, slug: str) -> ModelRegistry:
    row = ModelRegistry(
        openrouter_id=slug,
        display_name=slug,
        provider=slug.split("/")[0],
        supports_tools=False,
        runtime=ModelRuntime.DECISION,
    )
    db.add(row)
    await db.flush()
    return row


async def _reload(db: AsyncSession, slug: str) -> ModelRegistry:
    db.expire_all()
    row = await db.scalar(sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == slug))
    assert row is not None
    return row


async def test_a_model_that_answers_is_offered(db: AsyncSession) -> None:
    await _model(db, "typesafe/jev-1.13")
    report = await check_decision_models(db, _gateway(deciding()))

    assert report.answered == ["typesafe/jev-1.13"]
    row = await _reload(db, "typesafe/jev-1.13")
    assert (row.decisions_checked, row.decisions_refusal) == (DECISION_VERSION, None)
    assert "typesafe/jev-1.13" in {m.openrouter_id for m in await playable_models(db)}


async def test_an_unchecked_model_is_not_offered(db: AsyncSession) -> None:
    row = await _model(db, "newco/unknown")
    assert "newco/unknown" not in {m.openrouter_id for m in await playable_models(db)}
    assert any("not yet checked" in reason for reason in ineligible_reasons(row))


async def test_a_host_that_refuses_our_question_is_recorded_and_never_offered(
    db: AsyncSession,
) -> None:
    """Span-01, exactly: the catalogue describes it like Jev, and its host refuses a `choice`."""
    await _model(db, "respan/span-01")
    report = await check_decision_models(db, _gateway(_refusing(400, RESPAN_400)))

    assert report.refused == ["respan/span-01"]
    row = await _reload(db, "respan/span-01")
    assert row.decisions_checked == DECISION_VERSION
    assert row.decisions_refusal is not None and "only accepts noul" in row.decisions_refusal
    assert "respan/span-01" not in {m.openrouter_id for m in await playable_models(db)}
    assert any("refuses" in reason for reason in ineligible_reasons(row))


async def test_a_refusal_about_the_moment_records_nothing_and_is_asked_again(
    db: AsyncSession,
) -> None:
    """A rate limit says nothing about what the model can answer."""
    await _model(db, "jaredpalmer/kev-4b")
    report = await check_decision_models(db, _gateway(_refusing(429, "TPM limit reached")))

    assert report.deferred == ["jaredpalmer/kev-4b"]
    row = await _reload(db, "jaredpalmer/kev-4b")
    assert (row.decisions_checked, row.decisions_refusal) == (None, None)

    answering = deciding()
    await check_decision_models(db, _gateway(answering))
    assert len(answering.calls) == 1  # type: ignore[attr-defined]


async def test_a_checked_model_is_not_asked_again_until_the_version_changes(
    db: AsyncSession,
) -> None:
    """Once per model per version — never on a routine refresh."""
    await _model(db, "typesafe/jev-1.13")
    first = deciding()
    await check_decision_models(db, _gateway(first))
    again = deciding()
    await check_decision_models(db, _gateway(again))
    assert len(again.calls) == 0  # type: ignore[attr-defined]

    await db.execute(
        sa.update(ModelRegistry).values(decisions_checked="d1", decisions_refusal=None)
    )
    after_bump = deciding()
    await check_decision_models(db, _gateway(after_bump))
    assert len(after_bump.calls) == 1  # type: ignore[attr-defined]


async def test_the_check_asks_exactly_what_a_turn_asks(db: AsyncSession) -> None:
    """A check in a different shape could pass a model a real turn would be refused by."""
    await _model(db, "typesafe/jev-1.13")
    asked = deciding()
    await check_decision_models(db, _gateway(asked))
    (request,) = asked.calls  # type: ignore[attr-defined]
    assert set(request["questions"]) == {MOVE_QUESTION, ACTION_QUESTION}
    assert isinstance(request["state"], dict) and "position" in request["state"]
    assert decision_check.CHECK_MOVES == ("e4", "e5")


async def test_an_answer_that_is_not_an_answer_counts_as_a_refusal(db: AsyncSession) -> None:
    await _model(db, "odd/model")
    report = await check_decision_models(db, _gateway(deciding(malformed=True)))
    assert report.refused == ["odd/model"]

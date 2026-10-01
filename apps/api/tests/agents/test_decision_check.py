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
    assert "opponent_last_move" in request["state"]
    # Every legal move of the busiest position chess has, in one question.
    assert len(request["questions"][MOVE_QUESTION]["criteria"]) == 218


async def test_a_model_with_no_limit_is_recorded_as_having_none(db: AsyncSession) -> None:
    await _model(db, "typesafe/jev-1.13")
    report = await check_decision_models(db, _gateway(deciding()))
    assert report.limits == {}
    row = await _reload(db, "typesafe/jev-1.13")
    assert row.decisions_max_choices is None


async def test_a_limited_model_is_asked_smaller_until_it_answers_and_plays_in_heats(
    db: AsyncSession,
) -> None:
    """Tev, exactly: 20 options at most. Halving finds 13, the largest size asked under 20."""
    await _model(db, "togethercomputer/tev1-4b-experimental")
    host = deciding(max_choices=20)
    report = await check_decision_models(db, _gateway(host))

    assert report.answered == ["togethercomputer/tev1-4b-experimental"]
    assert report.limits == {"togethercomputer/tev1-4b-experimental": 13}
    row = await _reload(db, "togethercomputer/tev1-4b-experimental")
    assert (row.decisions_max_choices, row.decisions_refusal) == (13, None)
    assert row.openrouter_id in {m.openrouter_id for m in await playable_models(db)}

    sizes = [
        {len(q["criteria"]) for k, q in call["questions"].items() if k != ACTION_QUESTION}
        for call in host.calls  # type: ignore[attr-defined]
    ]
    assert sizes == [{218}, {109}, {54}, {27}, {13}]


def test_each_smaller_request_asks_every_question_at_its_size() -> None:
    """The host is shown its largest question as many times as a real turn could ask it."""
    for size in decision_check.PROBE_SIZES[1:]:
        body, expected = decision_check.check_request("vendor/model", size)
        heats = {k: q for k, q in body["questions"].items() if k != ACTION_QUESTION}
        assert len(heats) == -(-218 // size)
        assert {len(q["criteria"]) for q in heats.values()} == {size}
        assert ACTION_QUESTION in body["questions"]
        assert set(expected) == set(body["questions"])


async def test_a_model_refused_at_every_size_is_refused_after_six_requests(
    db: AsyncSession,
) -> None:
    await _model(db, "respan/span-01")
    host = _refusing(400, RESPAN_400)
    report = await check_decision_models(db, _gateway(host))

    assert report.refused == ["respan/span-01"]
    assert len(host.calls) == len(decision_check.PROBE_SIZES) == 6  # type: ignore[attr-defined]
    row = await _reload(db, "respan/span-01")
    assert row.decisions_max_choices is None


async def test_a_rate_limit_part_way_down_records_nothing(db: AsyncSession) -> None:
    """A limit found by a check that did not finish would be a guess, so none is written."""
    await _model(db, "upstage/solar-decide")
    state = {"calls": 0}
    refused = _refusing(422, "29 candidates exceed the 26 single-token labels")
    limited = _refusing(429, "Rate limit exceeded: free-models-per-day")

    async def refuse_then_limit(request: dict[str, Any]) -> dict[str, Any]:
        state["calls"] += 1
        return await (refused if state["calls"] == 1 else limited)(request)  # type: ignore[no-any-return]

    report = await check_decision_models(db, _gateway(refuse_then_limit))
    assert report.deferred == ["upstage/solar-decide"]
    row = await _reload(db, "upstage/solar-decide")
    assert (row.decisions_checked, row.decisions_max_choices, row.decisions_refusal) == (
        None,
        None,
        None,
    )


async def test_an_answer_that_is_not_an_answer_counts_as_a_refusal(db: AsyncSession) -> None:
    await _model(db, "odd/model")
    report = await check_decision_models(db, _gateway(deciding(malformed=True)))
    assert report.refused == ["odd/model"]


async def test_every_check_call_is_tagged_with_the_versions_session(db: AsyncSession) -> None:
    """Without one, the check's spend reached the key's usage and nothing that could explain it."""
    await _model(db, "togethercomputer/tev1-4b-experimental")
    host = deciding(max_choices=20)
    await check_decision_models(db, _gateway(host))
    sessions = {call.get("session_id") for call in host.calls}  # type: ignore[attr-defined]
    assert sessions == {f"decision-check-{DECISION_VERSION}"}


async def test_the_round_reports_what_the_answers_cost_and_refusals_add_nothing(
    db: AsyncSession,
) -> None:
    await _model(db, "togethercomputer/tev1-4b-experimental")
    await _model(db, "typesafe/jev-1.13")
    state = {"calls": 0}
    capped = deciding(max_choices=20, cost=0.0001)
    unlimited = deciding(cost=0.0002)

    async def host(request: dict[str, Any]) -> dict[str, Any]:
        state["calls"] += 1
        answer = capped if request["model"].startswith("together") else unlimited
        return await answer(request)  # type: ignore[no-any-return]

    report = await check_decision_models(db, _gateway(host))
    # Tev: four refusals, then one answer at 13. Jev: one answer at 218.
    assert state["calls"] == 6
    assert str(report.cost_usd) == "0.0003"
    assert str(report).endswith("$0.000300")

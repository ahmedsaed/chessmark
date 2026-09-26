"""Which games count when a decision model played (ADR-0049).

Each harness is held to its own version, and only when it played. The failure these prevent is a
chat-prompt bump retiring every game between two decision models — which ran no prompt at all —
or a decision-harness bump retiring chat games that never asked a decision question.
"""

from __future__ import annotations

from chessmark.bench.ratable import GameFacts, decision_era, era, judge
from chessmark.game import Termination

CURRENT = {"prompt_version": "v3", "tool_schema_version": "v4", "decision_version": "d1"}


def facts(harnesses: tuple[str, ...], **overrides: object) -> GameFacts:
    base: dict[str, object] = {
        "is_ranked": True,
        "termination": Termination.CHECKMATE,
        "prompt_version": "v3" if "llm" in harnesses else None,
        "tool_schema_version": "v4" if "llm" in harnesses else None,
        "decision_version": "d1" if "decision" in harnesses else None,
        "harnesses": harnesses,
        "model_slugs": ("a/one", "b/two"),
    }
    return GameFacts(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_game_between_decision_models_counts_with_no_prompt_version() -> None:
    assert judge(facts(("decision", "decision")), **CURRENT)


def test_a_new_chat_prompt_does_not_retire_decision_games() -> None:
    assert judge(facts(("decision", "decision")), **{**CURRENT, "prompt_version": "v4"})
    assert judge(facts(("decision", "decision")), **{**CURRENT, "tool_schema_version": "v5"})


def test_a_new_decision_version_retires_decision_games() -> None:
    verdict = judge(facts(("decision", "decision")), **{**CURRENT, "decision_version": "d2"})
    assert not verdict
    assert "decision harness d1" in verdict.reason


def test_a_new_decision_version_does_not_retire_chat_games() -> None:
    assert judge(facts(("llm", "llm")), **{**CURRENT, "decision_version": "d2"})


def test_a_mixed_game_is_held_to_both_versions() -> None:
    mixed = facts(("llm", "decision"))
    assert judge(mixed, **CURRENT)
    assert not judge(mixed, **{**CURRENT, "decision_version": "d2"})
    assert not judge(mixed, **{**CURRENT, "prompt_version": "v4"})


def test_a_minor_decision_bump_is_the_same_task() -> None:
    assert judge(facts(("decision", "decision"), decision_version="d1.1"), **CURRENT)


def test_a_record_from_before_harnesses_were_recorded_is_a_chat_game() -> None:
    legacy = facts((), prompt_version="v3", tool_schema_version="v4")
    assert judge(legacy, **CURRENT)
    assert not judge(legacy, **{**CURRENT, "prompt_version": "v4"})


def test_the_other_rules_still_apply_to_decision_games() -> None:
    assert not judge(facts(("decision", "decision"), is_ranked=False), **CURRENT)
    assert not judge(facts(("decision", "decision"), termination=Termination.ABANDONED), **CURRENT)
    assert not judge(facts(("decision", "decision"), model_slugs=("~a/latest",)), **CURRENT)


def test_a_decision_era_cannot_be_mistaken_for_a_chat_era() -> None:
    assert decision_era("d1") == "d1"
    assert decision_era("d1.3") == "d1"
    assert decision_era(None) == "?"
    assert decision_era("d1") != era("v3", "v4")

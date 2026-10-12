"""Reasoning levels, from the catalogue to the request (ADR-0067).

The shapes here are OpenRouter's own, taken from the catalogue when the ADR was written — one per
kind it actually serves. The trap they guard is `default_effort`: it is the effort used *when
reasoning is switched on*, not a statement that it is, and 19 models were off by default while
naming one.
"""

from __future__ import annotations

import pytest

from chessmark.agents import effort
from chessmark.agents.effort import AUTO, NONE, Levels, levels_from_catalogue, request_body
from chessmark.tournament.types import EFFORT_LEVELS


def test_a_model_that_cannot_reason_offers_only_none() -> None:
    assert levels_from_catalogue(supports_reasoning=False, reasoning=None) == Levels(
        offered=(NONE,), default=NONE
    )


def test_an_always_on_model_cannot_be_asked_not_to_reason() -> None:
    """GPT-6.1 Sol: mandatory, five efforts, medium by default. `none` would be refused upstream."""
    levels = levels_from_catalogue(
        supports_reasoning=True,
        reasoning={
            "mandatory": True,
            "default_enabled": True,
            "supported_efforts": ["max", "xhigh", "high", "medium", "low"],
            "default_effort": "medium",
        },
    )
    assert levels.offered == ("low", "medium", "high", "xhigh", "max")
    assert levels.default == "medium"


def test_a_default_effort_on_a_model_off_by_default_is_not_its_default() -> None:
    """Solar Mini 4 names `medium` and is off unless asked. Reading `default_effort` as the default
    would switch reasoning on for every game it plays, a different contestant from the one that
    played before."""
    levels = levels_from_catalogue(
        supports_reasoning=True,
        reasoning={
            "mandatory": False,
            "default_enabled": False,
            "supported_efforts": ["max", "xhigh", "high", "medium", "low", "minimal", "none"],
            "default_effort": "medium",
        },
    )
    assert levels.default == NONE
    assert NONE in levels.offered and "medium" in levels.offered


def test_an_unstated_default_is_read_as_off() -> None:
    """Haiku 5.5: optional, efforts listed, `default_enabled` absent."""
    levels = levels_from_catalogue(
        supports_reasoning=True,
        reasoning={
            "mandatory": False,
            "supported_efforts": ["max", "xhigh", "high", "medium", "low"],
            "default_effort": "medium",
        },
    )
    assert levels.default == NONE
    assert levels.offered == (NONE, "low", "medium", "high", "xhigh", "max")


def test_a_model_on_by_default_with_no_efforts_is_auto() -> None:
    """Reasoning on, nothing to tune: `auto` is the only "on" there is, and `none` the other."""
    levels = levels_from_catalogue(
        supports_reasoning=True, reasoning={"mandatory": False, "default_enabled": True}
    )
    assert levels == Levels(offered=(NONE, AUTO), default=AUTO)


def test_an_always_on_model_with_no_efforts_has_one_level() -> None:
    levels = levels_from_catalogue(supports_reasoning=True, reasoning={"mandatory": True})
    assert levels == Levels(offered=(AUTO,), default=AUTO)


def test_a_named_default_the_model_does_not_list_falls_back_to_auto() -> None:
    levels = levels_from_catalogue(
        supports_reasoning=True,
        reasoning={"mandatory": True, "supported_efforts": ["high"], "default_effort": "medium"},
    )
    assert levels.default == AUTO
    assert levels.offered == (AUTO, "high")


def test_the_tournament_package_spells_the_levels_the_same_way() -> None:
    """Duplicated because `tournament/` imports nothing; this keeps the copy honest."""
    assert EFFORT_LEVELS == effort.LEVELS


# ====================================================================== the request


def test_no_level_sends_nothing() -> None:
    """A seat from before ADR-0067: its requests must stay byte-identical."""
    assert request_body(None, model_slug="openai/gpt-6.1-sol", can_reason=True) is None


def test_none_switches_reasoning_off_only_where_there_is_some() -> None:
    assert request_body(NONE, model_slug="x/y", can_reason=True) == {"enabled": False}
    assert request_body(NONE, model_slug="x/y", can_reason=False) is None


def test_auto_and_an_effort() -> None:
    assert request_body(AUTO, model_slug="x/y", can_reason=True) == {"enabled": True}
    assert request_body("high", model_slug="openai/gpt-6.1-sol", can_reason=True) == {
        "enabled": True,
        "effort": "high",
    }


@pytest.mark.parametrize(
    ("slug", "budget_based"),
    [
        ("anthropic/claude-3.7-sonnet", True),
        ("anthropic/claude-sonnet-4.5", True),
        ("anthropic/claude-haiku-4.5", True),
        ("anthropic/claude-opus-5.5", False),
        ("anthropic/claude-sonnet-5", False),
        ("anthropic/claude-fable-5", False),
        ("openai/gpt-6.1-sol", False),
    ],
)
def test_which_models_take_a_budget(slug: str, budget_based: bool) -> None:
    assert effort.budget_based(slug) is budget_based


def test_a_budget_model_gets_a_budget_that_does_not_drift_with_the_call() -> None:
    """The point of sending it ourselves: OpenRouter would take it from this call's `max_tokens`,
    which is held back on the first call and shrinks as the window fills."""
    first = request_body(
        "medium",
        model_slug="anthropic/claude-sonnet-4.5",
        can_reason=True,
        max_tokens=16_000,
        nominal_max_tokens=64_000,
    )
    later = request_body(
        "medium",
        model_slug="anthropic/claude-sonnet-4.5",
        can_reason=True,
        max_tokens=64_000,
        nominal_max_tokens=64_000,
    )
    assert later == {"enabled": True, "max_tokens": 32_000}
    # Clamped under the call only when the call cannot hold it, and never above.
    assert first == {"enabled": True, "max_tokens": 15_999}


def test_an_adaptive_claude_keeps_its_effort() -> None:
    """Claude 5.x ignores a budget; sending one would lose the effort without a word."""
    body = request_body(
        "low",
        model_slug="anthropic/claude-opus-5.5",
        can_reason=True,
        max_tokens=64_000,
        nominal_max_tokens=64_000,
    )
    assert body == {"enabled": True, "effort": "low"}


def test_no_room_for_the_floor_falls_back_to_the_effort() -> None:
    body = request_body(
        "high",
        model_slug="anthropic/claude-sonnet-4.5",
        can_reason=True,
        max_tokens=900,
        nominal_max_tokens=64_000,
    )
    assert body == {"enabled": True, "effort": "high"}


def test_the_heaviest_levels_get_longer() -> None:
    assert effort.call_timeout("max", 600.0) == 1200.0
    assert effort.call_timeout("xhigh", 600.0) == 1200.0
    assert effort.call_timeout("high", 600.0) == 600.0
    assert effort.call_timeout(None, 600.0) == 600.0

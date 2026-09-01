"""Which games may move a rating (BENCH-03).

The most consequential rules in Phase 12, and almost none of them are arithmetic. A rating is only
as defensible as its exclusions, and every exclusion here corresponds to a game already sitting in
the database that would otherwise have corrupted the standings.
"""

from __future__ import annotations

import pytest

from chessmark.bench.ratable import (
    HARNESS_TERMINATIONS,
    RATED_TERMINATIONS,
    GameFacts,
    judge,
)
from chessmark.game import Termination


def facts(**overrides: object) -> GameFacts:
    base = {
        "is_ranked": True,
        "termination": Termination.CHECKMATE,
        "prompt_version": "v1",
        "pinned_providers": ("Baidu", "Google"),
        "used_providers": (("Baidu",), ("Google",)),
        "model_slugs": ("deepseek/deepseek-v4-pro", "google/gemini-3.7-flash"),
        "trash_talk_enabled": False,
    }
    return GameFacts(**{**base, **overrides})  # type: ignore[arg-type]


# ====================================================================== what counts


def test_a_clean_ranked_game_counts() -> None:
    assert judge(facts())


@pytest.mark.parametrize(
    "termination",
    [
        Termination.CHECKMATE,
        Termination.STALEMATE,
        Termination.RESIGNATION,
        Termination.THREEFOLD_REPETITION,
        Termination.FIFTY_MOVE_RULE,
        Termination.INSUFFICIENT_MATERIAL,
        Termination.AGREED_DRAW,
    ],
)
def test_every_chess_result_counts(termination: Termination) -> None:
    assert judge(facts(termination=termination))


@pytest.mark.parametrize(
    "termination",
    [
        Termination.ILLEGAL_MOVE_FORFEIT,
        Termination.ERROR_FORFEIT,
        Termination.CONTEXT_EXCEEDED,
    ],
)
def test_a_forfeit_counts(termination: Termination) -> None:
    """The benchmark's whole subject. A model that ran out of illegal-move retries, or never
    called a tool, failed at the task — excluding that would leave a leaderboard measuring only
    chess, which is the less interesting half."""
    assert judge(facts(termination=termination))


# ====================================================================== what does not


@pytest.mark.parametrize(
    "termination",
    [
        Termination.PLY_CAP,
        Termination.BUDGET_EXCEEDED,
        Termination.ABANDONED,
        # **`TIMEOUT` moved here, and this test asserted the opposite for a while.** AGENT-17 found
        # it measured the provider: the same model on two endpoints got two verdicts, and one lost
        # a game at ply 1 having never been served a completion. It stopped being a forfeit in
        # `game/referee.py` and stayed rated here, which is the half that actually reaches the
        # leaderboard.
        Termination.TIMEOUT,
        # **`TRUNCATED` moved here too, and for the same reason** (ADR-0024). ADR-0021 excluded the
        # case where *our* `max_tokens` cut the answer and kept the endpoint's own ceiling rated,
        # calling it a generous natural budget. It is not a budget, it is a property of the host:
        # Poolside stops `laguna-s-2.1` at 32,768 output tokens and we were asking for 64,000, so
        # a game the model had won by rook and two bishops against a lone pawn was scored a loss.
        Termination.TRUNCATED,
    ],
)
def test_a_harness_stop_does_not_count(termination: Termination) -> None:
    """Our ceiling, our budget, our provider — none of it is anything either model did.

    Not hypothetical: two such games turned out to be hiding a resignation and a checkmate one ply
    away, found by raising the budget and playing on. Recording them as draws would have been
    wrong twice over.
    """
    verdict = judge(facts(termination=termination))

    assert not verdict
    assert "harness" in verdict.reason


def test_an_adjudicated_game_does_not_count() -> None:
    """An administrator cancelling a game is not a chess result."""
    assert not judge(facts(termination=Termination.ADJUDICATION))


def test_an_unranked_game_does_not_count() -> None:
    assert not judge(facts(is_ranked=False))


def test_an_unfinished_game_does_not_count() -> None:
    assert not judge(facts(termination=None))


def test_trash_talk_disqualifies_even_a_ranked_game() -> None:
    """`create_match` already forces it off for ranked games. If one arrives with it on, that is a
    bug, and averaging a bug into the standings is worse than dropping the game (TALK-03)."""
    verdict = judge(facts(trash_talk_enabled=True))

    assert not verdict
    assert "trash talk" in verdict.reason


# ====================================================================== reproducibility


def test_a_game_served_by_two_endpoints_does_not_count() -> None:
    """The real one. An 80-ply game was served to DeepSeek by Baidu for 70 calls and StreamLake for
    33 — a blend of two endpoints that are measurably not equivalent, and nothing can reproduce it
    (ADR-0015)."""
    verdict = judge(facts(used_providers=(("Baidu", "StreamLake"), ("Google",))))

    assert not verdict
    assert "more than one endpoint" in verdict.reason


def test_a_game_that_drifted_off_its_pin_does_not_count() -> None:
    verdict = judge(
        facts(pinned_providers=("Baidu", "Google"), used_providers=(("Novita",), ("Google",)))
    )

    assert not verdict
    assert "pinned to Baidu" in verdict.reason


@pytest.mark.parametrize(
    "slug", ["~deepseek/deepseek-v4-flash-latest", "~google/gemini-flash-latest"]
)
def test_a_floating_alias_does_not_count(slug: str) -> None:
    """It points at different weights over time, so a rating across it rates nothing in
    particular."""
    verdict = judge(facts(model_slugs=(slug, "google/gemini-3.7-flash")))

    assert not verdict
    assert "floating alias" in verdict.reason


def test_an_older_prompt_version_does_not_count() -> None:
    """A different prompt is a different task, and mixing them silently is how a leaderboard stops
    meaning anything (BENCH-04)."""
    verdict = judge(facts(prompt_version="v0"), prompt_version="v1")

    assert not verdict
    assert "v0" in verdict.reason


def test_prompt_version_is_only_checked_when_one_is_named() -> None:
    """So a rating run can deliberately span versions if someone decides that is what they want."""
    assert judge(facts(prompt_version="v0"))


# ====================================================================== failing closed


def test_an_unclassified_termination_does_not_count() -> None:
    """A termination nobody has decided about must not quietly count. Adding a new one should
    require someone to say which side of the line it falls on."""
    unclassified = (
        set(Termination)
        - set(judge.__globals__["RATED_TERMINATIONS"])
        - set(judge.__globals__["HARNESS_TERMINATIONS"])
    )

    for termination in unclassified:
        verdict = judge(facts(termination=termination))
        assert not verdict, f"{termination} counts but is in neither classification"


def test_every_termination_is_deliberately_classified() -> None:
    """The inventory check. If this fails, someone added a `Termination` without deciding whether
    it is a finding about a player or a fact about our infrastructure."""
    unclassified = set(Termination) - RATED_TERMINATIONS - HARNESS_TERMINATIONS

    assert not unclassified, (
        f"unclassified terminations: {sorted(str(t) for t in unclassified)} — "
        "decide whether each is a finding about a player or about the harness"
    )


def test_the_reason_is_always_populated_on_a_refusal() -> None:
    """A methodology page that says "some games are excluded" invites disbelief; one that can print
    the reason per game does not."""
    for bad in (
        facts(is_ranked=False),
        facts(termination=None),
        facts(termination=Termination.PLY_CAP),
        facts(trash_talk_enabled=True),
        facts(used_providers=(("A", "B"), ("Google",))),
    ):
        verdict = judge(bad)
        assert not verdict
        assert verdict.reason, "a refusal with no reason cannot be explained to a reader"

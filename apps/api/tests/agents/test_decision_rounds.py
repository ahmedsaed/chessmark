"""A turn asked in heats when a model cannot take every legal move in one question (ADR-0059)."""

from __future__ import annotations

from typing import Any

import chess
import pytest

from chessmark.agents.decision_check import check_board
from chessmark.agents.decision_request import (
    ACTION_QUESTION,
    MOVE_QUESTION,
    build_request,
    split,
)
from chessmark.agents.decision_rounds import decide
from chessmark.agents.decisions import Decision, DecisionGateway
from chessmark.agents.llm import RetryPolicy
from chessmark.agents.scripted_decisions import deciding


async def _instant(_: float) -> None:
    return None


def _asking(decide_fn: Any, request: Any) -> Any:
    gateway = DecisionGateway(
        decide_fn=decide_fn, sleep_fn=_instant, retry=RetryPolicy(max_attempts=1)
    )

    async def ask(questions: dict[str, Any]) -> Decision:
        return await gateway.decide(request.body(model="vendor/model", questions=questions))

    return ask


@pytest.mark.parametrize("cap", [3, 6, 13, 20, 26])
def test_heats_are_even_never_over_the_cap_and_never_under_two(cap: int) -> None:
    """Under two options a host like Tev refuses the heat; over the cap, every host like it."""
    for count in range(cap + 1, 219):
        moves = [str(i) for i in range(count)]
        heats = split(moves, cap)
        sizes = [len(h) for h in heats]
        assert [m for h in heats for m in h] == moves
        assert max(sizes) <= cap
        assert min(sizes) >= 2
        assert max(sizes) - min(sizes) <= 1


async def test_a_limited_model_is_asked_heats_then_a_final_and_its_move_wins() -> None:
    board = chess.Board()
    request = build_request(board)
    host = deciding(moves=["Nf3"], max_choices=6)
    answer = await decide(request, max_choices=6, ask=_asking(host, request))

    assert answer.choice == "Nf3"
    assert len(host.calls) == 2  # type: ignore[attr-defined]
    assert set(answer.probabilities) <= set(request.moves)
    assert len(answer.probabilities) == 4
    assert answer.action == "play_on"


async def test_the_busiest_position_fits_the_smallest_limit_in_more_than_one_round() -> None:
    """218 moves under a limit of six: 37 heats, then 7, then 2, then the final."""
    request = build_request(check_board())
    # One from the middle of the list, so it has to win a heat in every round to be played.
    target = list(request.moves)[100]
    host = deciding(moves=[target], max_choices=6)
    answer = await decide(request, max_choices=6, ask=_asking(host, request))

    assert answer.choice == target
    calls = host.calls  # type: ignore[attr-defined]
    assert len(calls) == 4
    # The action question is asked once, with the first round, and never again.
    assert [ACTION_QUESTION in c["questions"] for c in calls] == [True, False, False, False]
    assert max(h.round for h in answer.heats) == 3


async def test_a_model_with_no_limit_is_asked_the_whole_request_once() -> None:
    request = build_request(chess.Board())
    host = deciding(moves=["e4"])
    answer = await decide(request, max_choices=None, ask=_asking(host, request))

    (call,) = host.calls  # type: ignore[attr-defined]
    assert call["questions"] == request.questions
    assert (answer.choice, answer.heats) == ("e4", [])


async def test_a_position_within_the_limit_is_asked_without_heats() -> None:
    request = build_request(chess.Board())
    host = deciding(moves=["e4"], max_choices=20)
    await decide(request, max_choices=20, ask=_asking(host, request))
    (call,) = host.calls  # type: ignore[attr-defined]
    assert call["questions"] == request.questions


async def test_a_forced_move_asks_only_what_to_do_with_the_turn() -> None:
    """One option is a question a host like Tev refuses, and its answer was never in doubt."""
    # In check from an unprotected queen on b2, with every other square covered by it.
    board = chess.Board("k7/8/8/8/8/8/1q6/K7 w - - 0 1")
    assert board.legal_moves.count() == 1
    request = build_request(board)
    assert set(request.questions) == {ACTION_QUESTION}

    host = deciding(max_choices=20)
    answer = await decide(request, max_choices=20, ask=_asking(host, request))
    assert answer.forced
    assert answer.choice == next(iter(request.moves))
    assert (answer.probabilities, answer.confidence) == ({}, None)


@pytest.mark.parametrize("cap", [1, 2])
async def test_a_limit_below_three_is_never_asked(cap: int) -> None:
    request = build_request(chess.Board())
    with pytest.raises(ValueError):
        await decide(request, max_choices=cap, ask=_asking(deciding(), request))


def test_a_heat_offers_only_its_moves_and_does_not_claim_they_are_all_of_them() -> None:
    request = build_request(chess.Board())
    groups = split(list(request.moves), 6)
    questions = request.heats(groups, with_action=True)
    for index, group in enumerate(groups):
        heat = questions[f"heat_{index + 1}"]
        assert list(heat["criteria"]) == group
        assert "these legal moves" in heat["instructions"]
    assert questions[ACTION_QUESTION] == request.questions[ACTION_QUESTION]
    final = request.final(["e4", "d4"])
    assert list(final) == [MOVE_QUESTION]
    assert list(final[MOVE_QUESTION]["criteria"]) == ["e4", "d4"]

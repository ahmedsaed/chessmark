"""What a decision model is shown and asked, and the version that pins it (ADR-0049)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import chess
import pytest

from chessmark.agents.decision_request import (
    ACCEPT_DRAW_QUESTION,
    CLAIM_DRAW_QUESTION,
    DECISION_VERSION,
    FIFTY_MOVES,
    MOVE_QUESTION,
    OFFER_DRAW_QUESTION,
    RECENT_PLIES,
    RESIGN_QUESTION,
    THREEFOLD,
    build_request,
)

RECORDED = Path(__file__).resolve().parent.parent / "fixtures" / "decision_requests"

#: A position with some history, a capture available, a piece at risk and castling rights left —
#: enough that every kind of fact appears somewhere in the recorded request.
OPENING = ("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5")

#: The queen trade from the first long real game: White has just taken on d7, and Black is nine
#: points "behind" for exactly the one ply before it takes back.
MID_EXCHANGE = ("e4", "e5", "Nf3", "Nf6", "Nxe5", "Nxe4", "Nf3", "d5", "Bb5+", "Qd7", "Bxd7+")


def _played(*sans: str) -> chess.Board:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def _recorded_requests() -> dict[str, object]:
    return {
        "opening": build_request(_played(*OPENING)).body(model="vendor/model"),
        "offered_and_claimable": build_request(
            _played(*OPENING), draw_offered=True, draw_claimable=THREEFOLD
        ).body(model="vendor/model"),
        "mid_exchange": build_request(_played(*MID_EXCHANGE)).body(model="vendor/model"),
    }


def test_the_request_is_the_one_recorded_for_this_version() -> None:
    """**Changing what a decision model is shown is a new task, and needs a new version.**

    The recorded file is named for `DECISION_VERSION`, so a changed request under the same version
    fails here — and bumping the version fails until the new request is recorded, which is the
    moment to write down in an ADR why it changed. Record with `RECORD_DECISION_REQUESTS=1`.
    """
    path = RECORDED / f"{DECISION_VERSION}.json"
    built = json.loads(json.dumps(_recorded_requests()))
    if os.environ.get("RECORD_DECISION_REQUESTS"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(built, indent=2, sort_keys=True) + "\n")
    if not path.exists():
        pytest.fail(
            f"no recorded request for {DECISION_VERSION}: record one with "
            "RECORD_DECISION_REQUESTS=1 and say in an ADR what changed"
        )
    assert built == json.loads(path.read_text()), (
        f"the decision request changed under {DECISION_VERSION}: bump DECISION_VERSION, record "
        "the new request, and write down why"
    )


def test_every_legal_move_is_offered_once_keyed_by_its_plain_notation() -> None:
    board = _played(*OPENING)
    request = build_request(board)
    criteria = request.questions[MOVE_QUESTION]["criteria"]
    assert (
        set(criteria)
        == set(request.moves)
        == {board.san(move).rstrip("+#") for move in board.legal_moves}
    )
    # The key is what the model reads; the value it maps to is what the referee plays.
    assert all(chess.Move.from_uci(uci) in board.legal_moves for uci in request.moves.values())


def test_neither_the_mating_move_nor_a_checking_one_is_marked() -> None:
    """**What the board shows, never a grade** (ADR-0049). The model reads check off the pieces
    and squares it is given; a label on the checking moves would pull it toward them."""
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    request = build_request(board)
    # The rules mention checkmate and stalemate; no move's description may say either word.
    described = json.dumps(request.questions[MOVE_QUESTION]["criteria"]).lower()
    assert "mate" not in described and "check" not in described
    assert "Qxf7" in request.moves and "Qxf7#" not in request.moves


def test_the_rules_that_decide_a_game_are_stated() -> None:
    """Invariant 12: a model cannot be scored against a condition it was never told about."""
    instructions = build_request(chess.Board()).questions[MOVE_QUESTION]["instructions"]
    for rule in ("resigns", "stalemate", "five times", "75 moves", "three times", "50 moves"):
        assert rule in instructions


def test_resigning_and_offering_are_asked_every_turn() -> None:
    questions = build_request(chess.Board()).questions
    assert questions[RESIGN_QUESTION]["type"] == "noul"
    assert questions[OFFER_DRAW_QUESTION]["type"] == "noul"
    assert CLAIM_DRAW_QUESTION not in questions
    assert ACCEPT_DRAW_QUESTION not in questions


def test_a_claim_is_asked_only_when_one_is_open_and_says_which() -> None:
    board = chess.Board()
    threefold = build_request(board, draw_claimable=THREEFOLD)
    fifty = build_request(board, draw_claimable=FIFTY_MOVES)
    assert threefold.questions[CLAIM_DRAW_QUESTION]["type"] == "noul"
    assert "three times" in threefold.state["draw_claim"]
    assert "Fifty moves" in fifty.state["draw_claim"]
    assert "draw_claim" not in build_request(board).state


def test_an_open_offer_is_answered_rather_than_countered() -> None:
    request = build_request(_played("e4"), draw_offered=True)
    assert request.questions[ACCEPT_DRAW_QUESTION]["type"] == "noul"
    assert request.state["draw_offer"] == "White has offered a draw."
    # Offering back while the opponent's own offer is open would be asking to accept it twice.
    assert OFFER_DRAW_QUESTION not in request.questions


def test_every_named_field_is_one_the_questions_can_read() -> None:
    """The skill's rule, and the reason the fields have the names they do: state no question reads
    is noise that costs accuracy."""
    request = build_request(_played(*OPENING), draw_offered=True, draw_claimable=THREEFOLD)
    assert set(request.state) == {
        "you_are",
        "position",
        "recent_moves",
        "opponent_last_move",
        "draw_offer",
        "draw_claim",
    }


def test_the_side_and_material_are_stated_from_the_movers_point_of_view() -> None:
    # White has won a knight: 1.e4 e5 2.Nf3 Nc6 3.Nxe5 Nxe5 would be even, so take the pawn only.
    board = _played("e4", "e5", "Nf3", "Nc6", "Nxe5")
    state = build_request(board).state
    assert state["you_are"] == "black"
    assert state["position"]["material"]["standing"] == "you are behind"
    assert state["opponent_last_move_captured"] == "your pawn on e5"
    assert state["opponent_last_move"] == "Nxe5"


def test_recent_moves_are_numbered_like_a_score_sheet_and_capped() -> None:
    assert build_request(_played("e4", "e5", "Nf3")).state["recent_moves"] == [
        "1.e4",
        "e5",
        "2.Nf3",
    ]

    long = _played(*(["Nf3", "Nf6", "Ng1", "Ng8"] * 12))
    recent = build_request(long).state["recent_moves"]
    assert len(recent) == RECENT_PLIES
    # 48 plies played, the last 40 kept: the list opens on ply 9, which is White's fifth move.
    assert recent[0] == "5.Nf3"


def test_a_cut_that_opens_on_a_black_move_marks_it_as_black() -> None:
    long = _played(*(["Nf3", "Nf6", "Ng1", "Ng8"] * 12), "Nf3")
    assert build_request(long).state["recent_moves"][0] == "5...Nf6"


def test_the_same_position_and_history_always_produce_the_same_bytes() -> None:
    first = build_request(_played(*OPENING)).body(model="m")
    second = build_request(_played(*OPENING)).body(model="m")
    assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)


def test_a_capture_is_stated_as_what_happened_and_never_as_advice() -> None:
    """**The facts say what is and what happened, never what to do** (ADR-0049). Counted at one
    instant, a queen trade read as nine points behind; the fix is history — what was taken and how
    material stood before — not a pointer at the recapture, which may not be the best move."""
    state = build_request(_played(*MID_EXCHANGE)).state
    assert state["opponent_last_move_captured"] == "your queen on d7"
    material = state["position"]["material"]
    assert material["standing"] == "you are behind"
    assert material["before_opponents_last_move"] == "level"
    text = json.dumps(state).lower()
    assert "take back" not in text and "recapture" not in text


def test_a_quiet_last_move_adds_nothing() -> None:
    state = build_request(_played("e4", "d5", "exd5", "Nf6", "Nc3")).state
    assert "opponent_last_move_captured" not in state
    assert "before_opponents_last_move" not in state["position"]["material"]
    assert state["position"]["material"]["standing"] == "you are behind"


def test_a_seat_that_may_not_offer_is_not_asked_to() -> None:
    request = build_request(chess.Board(), may_offer_draw=False)
    assert OFFER_DRAW_QUESTION not in request.questions
    assert RESIGN_QUESTION in request.questions

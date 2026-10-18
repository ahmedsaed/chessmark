"""A finished game's digest: the facts its page states in prose (ADR-0069).

The pure half is tested without a database. What matters in it: an excerpt is a **slice** of what
the model wrote and never a rewording of it (invariant 3), and a quote is never put on a move the
quoted model did not make.
"""

from __future__ import annotations

from typing import Any

import chess

from chessmark.db.digests import (
    DIGEST_VERSION,
    EXCERPT_CHARS,
    PlyRow,
    TurnText,
    build,
    excerpt,
    turns_from_events,
)
from chessmark.db.enums import EventType


def _plies(sans: list[str]) -> list[PlyRow]:
    board = chess.Board()
    rows = []
    for number, san in enumerate(sans, start=1):
        before = board.fen()
        move = board.parse_san(san)
        colour = "white" if board.turn == chess.WHITE else "black"
        board.push(move)
        rows.append(
            PlyRow(
                ply_number=number,
                colour=colour,
                san=san,
                uci=move.uci(),
                fen_before=before,
                fen_after=board.fen(),
            )
        )
    return rows


def _turn(ply: int, colour: str, move: str | None, reasoning: str = "", **extra: Any) -> TurnText:
    return TurnText(
        ply=ply, colour=colour, move=move, reasoning=[reasoning] if reasoning else [], **extra
    )


# ====================================================================== excerpts


def test_a_short_trace_is_quoted_whole() -> None:
    assert excerpt("I will play  e4.\n It opens lines.") == (
        "I will play e4. It opens lines.",
        False,
    )


def test_a_long_trace_is_cut_from_the_end_on_a_sentence() -> None:
    """The close of a trace is where the move and its reason are; the opening restates the board."""
    text = ("The position is quiet. " * 60) + "So Nf3 it is, because it guards h4 and e5."
    quoted, cut = excerpt(text)

    assert cut
    assert len(quoted) <= EXCERPT_CHARS
    assert quoted.endswith("guards h4 and e5.")
    assert quoted.startswith("The position"), "the cut lands on a sentence start"
    assert quoted in " ".join(text.split()), "an excerpt is a slice, never a rewording"


def test_a_trace_with_no_sentences_is_cut_on_a_word() -> None:
    quoted, cut = excerpt("word " * 400)
    assert cut
    assert quoted.startswith("word")


# ====================================================================== turns


def test_the_log_is_cut_into_turns_at_each_turn_started() -> None:
    turns = turns_from_events(
        [
            (EventType.GAME_STARTED, {}),
            (EventType.THINKING, {"reasoning": "before any turn"}),
            (EventType.TURN_STARTED, {"ply": 1, "colour": "white"}),
            (EventType.THINKING, {"reasoning": "centre"}),
            (EventType.ILLEGAL_ATTEMPT, {"move": "e5", "detail": "no pawn can reach e5"}),
            (EventType.MOVE_MADE, {"san": "e4"}),
            (EventType.TURN_STARTED, {"ply": 2, "colour": "black"}),
            (EventType.OUTPUT, {"content": "Mirror."}),
            (EventType.MOVE_MADE, {"san": "e5"}),
        ]
    )

    assert [(t.ply, t.colour, t.move) for t in turns] == [(1, "white", "e4"), (2, "black", "e5")]
    assert turns[0].reasoning == ["centre"], "text before the first turn belongs to none"
    assert turns[0].illegal == [{"move": "e5", "detail": "no pawn can reach e5"}]
    assert turns[1].text() == ("output", "Mirror."), "prose stands in when there is no reasoning"


# ====================================================================== the digest


SCHOLAR = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]


def test_the_digest_names_the_opening_and_the_final_material() -> None:
    digest = build(start_fen=chess.STARTING_FEN, plies=_plies(SCHOLAR), turns=[])

    assert digest["version"] == DIGEST_VERSION
    assert digest["opening"] == {"eco": "C23", "name": "Bishop's Opening", "ply": 3}
    assert digest["material"] == {"white": 39, "black": 38}


def test_the_first_illegal_attempt_is_quoted_with_what_was_tried() -> None:
    plies = _plies(["e4", "e5"])
    turns = [
        _turn(1, "white", "e4", "Push the king pawn."),
        _turn(
            2,
            "black",
            "e5",
            "Take on e4 with the queen.",
            illegal=[{"move": "Qxe4", "detail": "the queen cannot reach e4"}],
        ),
    ]
    moments = build(start_fen=chess.STARTING_FEN, plies=plies, turns=turns)["moments"]

    illegal = next(m for m in moments if m["kind"] == "illegal")
    assert illegal["attempted"] == "Qxe4"
    assert illegal["excerpt"] == "Take on e4 with the queen."
    assert illegal["colour"] == "black"


def test_trading_pawns_is_not_a_turning_point() -> None:
    sans = ["e4", "d5", "exd5", "Qxd5", "Nc3"]
    moments = build(start_fen=chess.STARTING_FEN, plies=_plies(sans), turns=[])["moments"]
    assert not [m for m in moments if m["kind"] == "capture"]


def test_the_earliest_of_two_equal_captures_is_the_turning_point() -> None:
    """3. Nxe4 takes a knight on ply 5, and 5... bxc6 a bishop on ply 10: both are worth 3."""
    sans = ["e4", "Nf6", "Nc3", "Nxe4", "Nxe4", "d5", "Bb5+", "c6", "Bxc6+", "bxc6", "Nf3"]
    moments = build(start_fen=chess.STARTING_FEN, plies=_plies(sans), turns=[])["moments"]
    capture = next(m for m in moments if m["kind"] == "capture")
    assert (capture["ply"], capture["san"], capture["captured"]) == (5, "Nxe4", "knight")


def test_a_capture_worth_a_piece_is_found_and_quoted() -> None:
    sans = ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qe5+", "Qe2", "Qxe2+", "Bxe2", "Nc6"]
    turns = [
        _turn(n, "white" if n % 2 else "black", san, f"thinking about {san}")
        for n, san in enumerate(sans, start=1)
    ]
    moments = build(start_fen=chess.STARTING_FEN, plies=_plies(sans), turns=turns)["moments"]

    capture = next(m for m in moments if m["kind"] == "capture")
    assert capture["san"] == "Qxe2+"
    assert capture["captured"] == "queen"
    assert capture["excerpt"] == "thinking about Qxe2+"


def test_the_last_turn_is_the_final_moment_even_without_a_move() -> None:
    """A forfeit or a resignation ends the game on a turn that played nothing."""
    plies = _plies(["e4", "e5"])
    turns = [
        _turn(1, "white", "e4"),
        _turn(2, "black", "e5"),
        _turn(3, "white", None, "I resign; the position is lost."),
    ]
    final = build(start_fen=chess.STARTING_FEN, plies=plies, turns=turns)["moments"][-1]

    assert final["kind"] == "final"
    assert final["ply"] == 3
    assert final["san"] is None
    assert final["excerpt"] == "I resign; the position is lost."


def test_a_person_s_last_move_is_not_quoted_in_the_model_s_words() -> None:
    """A person's seat logs no turn. The model's earlier words must not be pinned on their move."""
    plies = _plies(["e4", "e5", "Qh5"])
    turns = [_turn(2, "black", "e5", "Symmetry.")]  # white is a person: no turns logged
    final = build(start_fen=chess.STARTING_FEN, plies=plies, turns=turns)["moments"][-1]

    assert final["kind"] == "final"
    assert final["ply"] == 3
    assert final["san"] == "Qh5"
    assert final["excerpt"] is None


def test_one_turn_is_never_quoted_twice() -> None:
    plies = _plies(SCHOLAR)
    turns = [
        _turn(n, "white" if n % 2 else "black", san, f"on {san}", illegal=[{"move": "x"}])
        if n == 7
        else _turn(n, "white" if n % 2 else "black", san, f"on {san}")
        for n, san in enumerate(SCHOLAR, start=1)
    ]
    moments = build(start_fen=chess.STARTING_FEN, plies=plies, turns=turns)["moments"]
    plies_quoted = [m["ply"] for m in moments]
    assert len(plies_quoted) == len(set(plies_quoted))

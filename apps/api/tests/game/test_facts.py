"""The facts a decision model is handed about a position and its moves (ADR-0049).

Every one is a claim a model will act on, so each is checked against a position where it is true
and one where it is not — a fact that is always `True` in the fixtures is a fact nobody tested.
"""

from __future__ import annotations

import chess

from chessmark.game.facts import (
    MoveFacts,
    PositionFacts,
    legal_move_facts,
    move_facts,
    position_facts,
)


def _facts(fen: str, san: str) -> object:
    board = chess.Board(fen)
    return move_facts(board, board.parse_san(san))


def test_a_quiet_move_names_its_piece_and_squares_and_nothing_else() -> None:
    board = chess.Board()
    facts = move_facts(board, board.parse_san("Nf3"))
    assert (facts.piece, facts.from_square, facts.to_square) == ("knight", "g1", "f3")
    assert facts.captures is None
    assert facts.promotes_to is None
    assert facts.castles is None
    assert not facts.repeats_position


def test_a_capture_names_what_it_takes() -> None:
    # 1.e4 d5: exd5 takes a pawn.
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2")
    facts = move_facts(board, board.parse_san("exd5"))
    assert facts.captures == "pawn"


def test_en_passant_names_the_pawn_it_removes_from_another_square() -> None:
    # The destination square is empty, so reading the capture off it would say "nothing".
    board = chess.Board("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3")
    facts = move_facts(board, board.parse_san("exf6"))
    assert facts.captures == "pawn"


def test_castling_says_which_side() -> None:
    fen = "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"
    board = chess.Board(fen)
    assert move_facts(board, board.parse_san("O-O")).castles == "kingside"
    assert move_facts(board, board.parse_san("O-O-O")).castles == "queenside"


def test_a_promotion_names_the_piece() -> None:
    board = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    promotions = {m.promotes_to for m in legal_move_facts(board) if m.piece == "pawn"}
    assert promotions == {"queen", "rook", "bishop", "knight"}


def test_neither_check_nor_mate_is_a_fact() -> None:
    """**No label that grades a move** (ADR-0049). Mate is the answer, not a fact; check was a fact
    and was removed, because a label on some moves pulls a model toward them."""
    # Scholar's mate: Qxf7 is check *and* mate.
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    facts = move_facts(board, board.parse_san("Qxf7#"))
    assert facts.san == "Qxf7"
    assert not any("check" in name or "mate" in name for name in MoveFacts.__dataclass_fields__)
    assert all("#" not in m.san and "+" not in m.san for m in legal_move_facts(board))


def test_a_move_that_repeats_a_position_says_so() -> None:
    board = chess.Board()
    for san in ("Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6"):
        board.push_san(san)
    # Ng1 now recreates the position after 2.Ng1 — the one fact that lets a seat see a draw coming.
    assert move_facts(board, board.parse_san("Ng1")).repeats_position
    assert not move_facts(board, board.parse_san("Nc3")).repeats_position


def test_working_out_the_facts_leaves_the_board_as_it_found_it() -> None:
    board = chess.Board()
    board.push_san("e4")
    before = (board.fen(), list(board.move_stack))
    legal_move_facts(board)
    assert (board.fen(), list(board.move_stack)) == before


def test_moves_come_back_in_a_stable_order() -> None:
    board = chess.Board()
    sans = [m.san for m in legal_move_facts(board)]
    assert sans == sorted(sans)
    assert len(sans) == 20


def test_the_position_lists_pieces_and_material() -> None:
    # N+2P (5) against B+P (4), Black to move.
    board = chess.Board("6k1/8/3p4/4N3/3P2b1/8/7P/6K1 b - - 0 1")
    facts = position_facts(board)
    assert facts.side_to_move == "black"
    assert "knight on e5" in facts.white_pieces
    assert "bishop on g4" in facts.black_pieces
    assert (facts.white_material, facts.black_material) == (5, 4)


def test_nothing_that_has_to_be_worked_out_is_a_fact() -> None:
    """**What a board shows, and nothing a board does not** (ADR-0049) — the chat seats' line.
    Check, mate, attacks and defences all have to be worked out from the pieces, and each was
    removed because a fact on some moves and not others pulls a model toward or away from them."""
    graded = {"check", "checks", "mate", "attacked", "attacks", "defended", "risk", "hanging"}
    fields = [*MoveFacts.__dataclass_fields__, *PositionFacts.__dataclass_fields__]
    # Whole words, so `white_material` is not read as "mate". `in_check` is the seat's own
    # position — it has to know it must answer a check — and says nothing about any move.
    assert [f for f in fields if graded & set(f.split("_")) and f != "in_check"] == []

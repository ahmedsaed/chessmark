"""Full FIDE rule coverage (GAME-02).

Every terminal condition and special move gets an explicit test. These are the rules an agent
will eventually be judged against, so a gap here is a benchmark that scores the wrong thing.
"""

from __future__ import annotations

import pytest

from chessmark.game import ChessBoard, GameResult, Referee, Termination

# --------------------------------------------------------------------- castling


def _play(referee: Referee, moves: list[str]) -> None:
    for move in moves:
        referee.play(move)


def test_kingside_castling_both_colours() -> None:
    referee = Referee()
    _play(referee, ["e4", "e5", "Nf3", "Nf6", "Bc4", "Bc5", "O-O", "O-O"])

    board = referee.board.raw
    assert board.piece_at(6).symbol() == "K", "white king should be on g1"
    assert board.piece_at(5).symbol() == "R", "white rook should be on f1"
    assert board.piece_at(62).symbol() == "k", "black king should be on g8"
    assert board.piece_at(61).symbol() == "r", "black rook should be on f8"


def test_queenside_castling_both_colours() -> None:
    referee = Referee()
    _play(referee, ["d4", "d5", "Nc3", "Nc6", "Bf4", "Bf5", "Qd2", "Qd7", "O-O-O", "O-O-O"])

    board = referee.board.raw
    assert board.piece_at(2).symbol() == "K", "white king should be on c1"
    assert board.piece_at(3).symbol() == "R", "white rook should be on d1"
    assert board.piece_at(58).symbol() == "k", "black king should be on c8"
    assert board.piece_at(59).symbol() == "r", "black rook should be on d8"


def test_castling_rejected_through_check() -> None:
    # White may not castle kingside while f1 is attacked by the bishop on a6.
    board = ChessBoard("4k3/8/b7/8/8/8/8/4K2R w K - 0 1")
    assert "O-O" not in board.legal_moves_san()


def test_castling_rights_lost_after_king_moves() -> None:
    referee = Referee()
    _play(referee, ["e4", "e5", "Ke2", "Ke7", "Ke1", "Ke8"])
    assert "O-O" not in referee.board.legal_moves_san()


# --------------------------------------------------------------------- en passant


def test_en_passant_capture() -> None:
    referee = Referee()
    _play(referee, ["e4", "d5", "e5", "f5"])

    view = referee.board.view()
    assert view.en_passant == "f6", "f6 should be available for en passant"

    outcome = referee.play("exf6")
    assert outcome.move.is_en_passant
    assert outcome.move.is_capture
    assert referee.board.raw.piece_at(37) is None, "the captured pawn on f5 should be gone"


def test_en_passant_expires_after_one_move() -> None:
    referee = Referee()
    # Nc6 rather than Nf6: a knight on f6 would make exf6 legal as an ordinary capture,
    # which would hide whether the en passant right actually expired.
    _play(referee, ["e4", "d5", "e5", "f5", "Nf3", "Nc6"])
    assert referee.board.view().en_passant is None
    assert "exf6" not in referee.board.legal_moves_san()


# --------------------------------------------------------------------- promotion

PROMOTION_FEN = "8/P6k/8/8/8/8/8/K7 w - - 0 1"


@pytest.mark.parametrize(
    ("move", "symbol", "uci"),
    [
        ("a8=Q", "Q", "a7a8q"),
        ("a8=R", "R", "a7a8r"),
        ("a8=B", "B", "a7a8b"),
        ("a8=N", "N", "a7a8n"),
    ],
)
def test_promotion_to_every_piece(move: str, symbol: str, uci: str) -> None:
    board = ChessBoard(PROMOTION_FEN)
    played = board.push(move)

    assert played.promotion == symbol
    assert played.uci == uci
    promoted = board.raw.piece_at(56)
    assert promoted is not None
    assert promoted.symbol() == symbol


def test_promotion_accepts_uci_notation() -> None:
    board = ChessBoard(PROMOTION_FEN)
    played = board.push("a7a8n")
    assert played.san == "a8=N"
    assert played.promotion == "N"


# --------------------------------------------------------------------- checkmate


def test_fools_mate_is_checkmate() -> None:
    referee = Referee()
    _play(referee, ["f3", "e5", "g4"])
    outcome = referee.play("Qh4")

    assert outcome.move.is_checkmate
    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.CHECKMATE
    assert outcome.outcome.result is GameResult.BLACK_WINS
    assert referee.is_over


# --------------------------------------------------------------------- stalemate


def test_stalemate_detected_when_side_to_move_has_no_moves() -> None:
    # After Qg6: black king on h8 is not in check, but h7, g7 and g8 are all covered.
    referee = Referee(start_fen="7k/5K2/8/8/8/8/8/6Q1 w - - 0 1")
    outcome = referee.play("Qg6")

    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.STALEMATE
    assert outcome.outcome.result is GameResult.DRAW
    assert outcome.outcome.winner is None


# --------------------------------------------------------- insufficient material


@pytest.mark.parametrize(
    ("fen", "label"),
    [
        ("8/8/8/4k3/8/8/8/4K3 w - - 0 1", "king versus king"),
        ("8/8/8/4k3/8/8/8/3BK3 w - - 0 1", "king and bishop versus king"),
        ("8/8/8/4k3/8/8/8/3NK3 w - - 0 1", "king and knight versus king"),
        ("5b2/8/8/4k3/8/8/8/2B1K3 w - - 0 1", "same-coloured bishops"),
    ],
)
def test_insufficient_material_cases(fen: str, label: str) -> None:
    assert ChessBoard(fen).is_insufficient_material(), label


def test_rook_is_sufficient_material() -> None:
    assert not ChessBoard("7k/8/8/8/8/8/8/R3K3 w - - 0 1").is_insufficient_material()


def test_insufficient_material_ends_the_game() -> None:
    # White captures the last black piece, leaving king and bishop against a bare king.
    referee = Referee(start_fen="8/8/8/3k4/3n4/8/8/3K2B1 w - - 0 1")
    outcome = referee.play("Bxd4")

    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.INSUFFICIENT_MATERIAL
    assert outcome.outcome.result is GameResult.DRAW


# --------------------------------------------------------------------- repetition


def test_threefold_repetition_draws_automatically() -> None:
    referee = Referee()
    # Shuffle both knights out and back twice; the starting position occurs three times.
    _play(referee, ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1"])
    outcome = referee.play("Ng8")

    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.THREEFOLD_REPETITION
    assert outcome.outcome.result is GameResult.DRAW


def test_two_repetitions_do_not_draw() -> None:
    referee = Referee()
    _play(referee, ["Nf3", "Nf6", "Ng1", "Ng8"])
    assert not referee.is_over


# --------------------------------------------------------------------- fifty moves


def test_fifty_move_rule_draws() -> None:
    # Halfmove clock at 99; a quiet rook move takes it to 100.
    referee = Referee(start_fen="7k/8/8/8/8/8/8/R3K3 w Q - 99 60")
    outcome = referee.play("Ra2")

    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.FIFTY_MOVE_RULE
    assert outcome.outcome.result is GameResult.DRAW


def test_pawn_move_resets_the_halfmove_clock() -> None:
    referee = Referee(start_fen="7k/8/8/8/8/8/P7/4K3 w - - 99 60")
    referee.play("a4")

    assert not referee.is_over
    assert referee.board.view().halfmove_clock == 0

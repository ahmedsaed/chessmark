"""The agent-facing view of a position.

Everything here ends up in an LLM prompt, so these tests are as much about the contract with the
model as about correctness.
"""

from __future__ import annotations

from chessmark.game import ChessBoard

RUY_LOPEZ = "r1bqk2r/1pppbppp/p1n2n2/4p3/B3P3/5N2/PPPP1PPP/RNBQ1RK1 w kq - 4 6"


def test_starting_position_has_twenty_legal_moves() -> None:
    board = ChessBoard()
    assert len(board.legal_moves()) == 20
    assert board.side_to_move == "white"
    assert board.ply == 0


def test_legal_moves_are_sorted_and_stable() -> None:
    board = ChessBoard(RUY_LOPEZ)
    first = board.legal_moves_san()
    second = board.legal_moves_san()

    assert first == second, "the same position must produce the same list"
    assert first == sorted(first)


def test_legal_moves_carry_their_flags() -> None:
    board = ChessBoard(RUY_LOPEZ)
    by_san = {move.san: move for move in board.legal_moves()}

    assert by_san["Bxc6"].is_capture
    assert not by_san["d3"].is_capture
    assert by_san["Bxc6"].uci == "a4c6"


def test_checkmate_flag_is_set_on_the_mating_move() -> None:
    # Back-rank mate: Ra8 is mate.
    board = ChessBoard("6k1/5ppp/8/8/8/8/8/R3K3 w Q - 0 1")
    by_san = {move.san: move for move in board.legal_moves()}
    assert by_san["Ra8#"].is_checkmate
    assert by_san["Ra8#"].is_check


def test_view_reports_the_whole_position() -> None:
    view = ChessBoard(RUY_LOPEZ).view()

    assert view.fen == RUY_LOPEZ
    assert view.side_to_move == "white"
    assert view.fullmove_number == 6
    assert view.halfmove_clock == 4
    assert view.castling_rights == "kq"
    assert view.en_passant is None
    assert view.in_check is False
    assert view.legal_move_count == len(ChessBoard(RUY_LOPEZ).legal_moves())


def test_material_is_balanced_at_the_start() -> None:
    material = ChessBoard().material()
    assert material.white == material.black == 39
    assert material.balance == 0


def test_material_balance_favours_the_side_that_is_up() -> None:
    # White is a whole queen ahead.
    material = ChessBoard("4k3/8/8/8/8/8/8/3QK3 w - - 0 1").material()
    assert material.white == 9
    assert material.black == 0
    assert material.balance == 9


def test_ascii_diagram_is_labelled_and_oriented() -> None:
    diagram = ChessBoard().ascii(perspective="white")
    lines = diagram.splitlines()

    assert lines[0].split() == list("abcdefgh")
    assert lines[-1].split() == list("abcdefgh")
    assert lines[1].startswith("8 "), "rank 8 sits at the top from White's perspective"
    assert lines[8].startswith("1 ")
    assert "r n b q k b n r" in lines[1], "black pieces are lowercase"
    assert "R N B Q K B N R" in lines[8], "white pieces are uppercase"


def test_ascii_diagram_flips_for_black() -> None:
    lines = ChessBoard().ascii(perspective="black").splitlines()
    assert lines[1].startswith("1 "), "rank 1 sits at the top from Black's perspective"
    assert lines[0].split() == list("hgfedcba")


def test_empty_squares_render_as_dots() -> None:
    assert ". . . . . . . ." in ChessBoard().ascii()


def test_history_is_recorded_in_algebraic_notation() -> None:
    board = ChessBoard()
    for move in ["e4", "e5", "Nf3", "Nc6"]:
        board.push(move)

    assert board.history_san() == ["e4", "e5", "Nf3", "Nc6"]
    assert board.ply == 4
    assert board.view().move_history_san == ["e4", "e5", "Nf3", "Nc6"]


def test_history_is_a_copy_not_the_live_list() -> None:
    board = ChessBoard()
    board.push("e4")
    history = board.history_san()
    history.append("tampered")

    assert board.history_san() == ["e4"]


def test_copy_is_independent() -> None:
    board = ChessBoard()
    board.push("e4")
    clone = board.copy()
    clone.push("e5")

    assert board.ply == 1
    assert clone.ply == 2
    assert board.history_san() == ["e4"]


def test_root_fen_is_preserved_from_a_custom_start() -> None:
    board = ChessBoard(RUY_LOPEZ)
    board.push("d3")
    assert board.root_fen == RUY_LOPEZ
    assert board.fen != RUY_LOPEZ


def test_check_is_reported() -> None:
    board = ChessBoard("4k3/8/8/8/8/8/8/4R2K b - - 0 1")
    assert board.in_check
    assert board.view().in_check

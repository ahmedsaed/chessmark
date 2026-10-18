"""Naming a game's opening from the Lichess table."""

from __future__ import annotations

from chessmark.game.openings import MAX_BOOK_PLIES, _book, identify

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]


def test_a_known_line_is_named() -> None:
    opening = identify(ITALIAN)
    assert opening is not None
    assert opening.eco == "C50"
    assert opening.name == "Italian Game: Giuoco Piano"
    assert opening.ply == 6


def test_a_transposition_is_named_for_the_position_not_the_move_order() -> None:
    """1. c4 e6 2. Nc3 d5 3. d4 and 1. d4 d5 2. c4 e6 3. Nc3 are one position."""
    english_order = identify(["c4", "e6", "Nc3", "d5", "d4"])
    queens_order = identify(["d4", "d5", "c4", "e6", "Nc3"])
    assert english_order is not None and queens_order is not None
    assert english_order.name == queens_order.name
    assert "Queen's Gambit Declined" in english_order.name


def test_the_deepest_named_position_wins_after_the_game_leaves_the_book() -> None:
    """Named for where it last was in the book, not dropped for having left it."""
    left = identify([*ITALIAN, "a3", "a6", "h3", "h6"])
    assert left is not None
    assert left.ply <= len(ITALIAN) + 4
    assert left.name.startswith("Italian Game")


def test_a_game_from_another_position_has_no_opening() -> None:
    fen = "8/8/8/4k3/8/8/4K3/8 w - - 0 1"
    assert identify(["Kd2"], start_fen=fen) is None


def test_a_move_that_does_not_replay_stops_the_walk_rather_than_raising() -> None:
    opening = identify(["e4", "e5", "Qxf7"])
    assert opening is not None
    assert opening.ply == 2


def test_a_long_game_is_walked_no_deeper_than_the_book() -> None:
    """A named position past the cap is never reached: a 300-ply game costs what a 40-ply one does.

    Knights out and back keep passing through 1. Nf3 Nf6, which is named; the Italian is reached
    only *after* the cap, and must not be what the game is called.
    """
    shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"] * 10
    opening = identify([*shuffle, *ITALIAN])
    assert opening is not None
    assert opening.ply <= MAX_BOOK_PLIES
    assert not opening.name.startswith("Italian Game")


def test_the_whole_table_replays() -> None:
    """Every row in the file is a legal line. A bad row would raise inside `_book`."""
    assert len(_book()) > 3000

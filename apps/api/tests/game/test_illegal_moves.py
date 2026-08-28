"""Illegal-move rejection (ADR-0002 / AGENT-05).

The contract: *every* rejection carries the complete legal move list and a human-readable
explanation. The benchmark measures whether a model can act correctly given complete information,
so an unhelpful error would be measuring the wrong thing.
"""

from __future__ import annotations

import pytest

from chessmark.game import ChessBoard, IllegalMoveError, IllegalMoveReason

# Ruy Lopez after 3...a6 — a real position with plenty of legal moves.
OPEN_POSITION = "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"


def _reject(fen: str, move: str) -> IllegalMoveError:
    with pytest.raises(IllegalMoveError) as caught:
        ChessBoard(fen).push(move)
    return caught.value


@pytest.mark.parametrize(
    "move",
    [
        "Qh5",  # well-formed algebraic, no such legal move
        "banana",  # not notation at all
        "d2d5",  # UCI, piece cannot reach
        "d7d5",  # UCI, opponent's piece
        "c3c4",  # UCI, empty origin square
        "",  # nothing at all
        "   ",  # whitespace only
        "Nf9",  # off the board
        "O-O-O",  # castling that is not available
    ],
)
def test_every_rejection_carries_the_full_legal_move_list(move: str) -> None:
    error = _reject(OPEN_POSITION, move)

    assert error.legal_moves_san, f"{move!r} rejected without a legal move list"
    assert error.detail.strip(), f"{move!r} rejected without an explanation"
    assert error.fen == OPEN_POSITION
    assert error.move == move

    # The list must be genuinely usable: every entry should actually be playable.
    board = ChessBoard(OPEN_POSITION)
    assert set(error.legal_moves_san) == set(board.legal_moves_san())


def test_serialised_error_is_complete() -> None:
    payload = _reject(OPEN_POSITION, "Qh5").as_dict()

    assert payload["ok"] is False
    assert payload["error"] == "illegal_move"
    assert payload["move"] == "Qh5"
    assert payload["fen"] == OPEN_POSITION
    assert payload["legal_moves_san"]
    assert payload["detail"]
    assert payload["reason"]


# --------------------------------------------------------------------- reasons


def test_unparseable_move_is_flagged_as_notation() -> None:
    error = _reject(OPEN_POSITION, "banana")
    assert error.reason is IllegalMoveReason.INVALID_NOTATION
    assert "algebraic" in error.detail


def test_empty_move_is_flagged_as_notation() -> None:
    assert _reject(OPEN_POSITION, "").reason is IllegalMoveReason.INVALID_NOTATION


def test_empty_origin_square_is_named() -> None:
    error = _reject(OPEN_POSITION, "c3c4")
    assert error.reason is IllegalMoveReason.NO_PIECE
    assert "c3" in error.detail


def test_moving_the_opponents_piece_is_explained() -> None:
    error = _reject(OPEN_POSITION, "d7d5")
    assert error.reason is IllegalMoveReason.WRONG_COLOR
    assert "Black" in error.detail
    assert "White" in error.detail


def test_unreachable_destination_names_the_piece() -> None:
    error = _reject(OPEN_POSITION, "d2d5")
    assert error.reason is IllegalMoveReason.NOT_REACHABLE
    assert "pawn" in error.detail
    assert "d2" in error.detail and "d5" in error.detail


def test_pinned_piece_is_told_it_is_pinned() -> None:
    # The knight on e4 is pinned to the king on e1 by the rook on e8.
    error = _reject("4r2k/8/8/8/4N3/8/8/4K3 w - - 0 1", "Nf6")
    assert error.reason is IllegalMoveReason.LEAVES_KING_IN_CHECK
    assert "check" in error.detail


def test_pinned_piece_via_uci_is_told_it_is_pinned() -> None:
    error = _reject("4r2k/8/8/8/4N3/8/8/4K3 w - - 0 1", "e4f6")
    assert error.reason is IllegalMoveReason.LEAVES_KING_IN_CHECK
    assert "check" in error.detail


def test_ambiguous_move_asks_for_disambiguation() -> None:
    # Both the b1 and f1 knights can reach d2.
    error = _reject("4k3/8/8/8/8/8/8/1N3N1K w - - 0 1", "Nd2")
    assert error.reason is IllegalMoveReason.AMBIGUOUS
    assert "ambiguous" in error.detail.lower()
    assert "Nbd2" in error.detail


# --------------------------------------------------------------------- accepted input


@pytest.mark.parametrize("move", ["e4", "e2e4", " e4 ", "E2E4"])
def test_equivalent_notations_are_all_accepted(move: str) -> None:
    board = ChessBoard()
    played = board.push(move)
    assert played.san == "e4"
    assert played.uci == "e2e4"


def test_a_rejected_move_does_not_change_the_board() -> None:
    board = ChessBoard()
    before = board.fen

    with pytest.raises(IllegalMoveError):
        board.push("Qh5")

    assert board.fen == before
    assert board.ply == 0
    assert board.history_san() == []


# ====================================================================== promotions


class TestAMissingPromotion:
    """A pawn reaching the last rank without saying what it becomes.

    Its own reason, because every other explanation is false: the pawn *can* go there. It used to
    answer `not_reachable` — "the pawn on e7 cannot move to e8" — to a player that had found the
    move and left out a qualifier, and charged it an illegal attempt for the privilege.
    """

    BOARD = "8/4P3/8/8/8/8/8/K6k w - - 0 1"

    def test_uci_without_a_piece_says_which_are_available(self) -> None:
        board = ChessBoard(fen=self.BOARD)

        with pytest.raises(IllegalMoveError) as raised:
            board.push("e7e8")

        assert raised.value.reason is IllegalMoveReason.MISSING_PROMOTION
        for option in ("e8=Q", "e8=R", "e8=B", "e8=N"):
            assert option in raised.value.detail

    def test_algebraic_without_a_piece_says_the_same(self) -> None:
        """The likelier mistake of the two: a model writing `e8` rather than `e8=Q`."""
        board = ChessBoard(fen=self.BOARD)

        with pytest.raises(IllegalMoveError) as raised:
            board.push("e8")

        assert raised.value.reason is IllegalMoveReason.MISSING_PROMOTION

    def test_the_uci_hint_names_a_queen(self) -> None:
        """Under-promotion is the rarity; the example should show the answer most players want."""
        board = ChessBoard(fen=self.BOARD)

        with pytest.raises(IllegalMoveError) as raised:
            board.push("e7e8")

        assert "'q'" in raised.value.detail

    def test_the_full_legal_list_still_comes_with_it(self) -> None:
        """ADR-0002 holds for this reason like every other: an illegal result carries the moves."""
        board = ChessBoard(fen=self.BOARD)

        with pytest.raises(IllegalMoveError) as raised:
            board.push("e8")

        assert "e8=Q" in raised.value.legal_moves_san

    def test_a_named_promotion_is_played(self) -> None:
        assert ChessBoard(fen=self.BOARD).push("e8=Q").san == "e8=Q"
        assert ChessBoard(fen=self.BOARD).push("e7e8n").san == "e8=N"

    def test_an_ordinary_move_to_the_last_rank_is_not_a_promotion(self) -> None:
        """A rook reaching the eighth rank is just a move, and must not be asked what it becomes."""
        board = ChessBoard(fen="8/8/8/8/8/8/R7/K6k w - - 0 1")

        assert board.push("Ra8").san == "Ra8"

    def test_a_genuinely_unreachable_square_still_says_so(self) -> None:
        """The new branch must not swallow the old answer: a pawn that cannot reach the rank at
        all is `not_reachable`, and telling it to name a piece would be nonsense."""
        board = ChessBoard(fen="8/8/8/8/4P3/8/8/K6k w - - 0 1")

        with pytest.raises(IllegalMoveError) as raised:
            board.push("e8")

        assert raised.value.reason is IllegalMoveReason.NOT_REACHABLE

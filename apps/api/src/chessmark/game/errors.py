"""Structured errors for the chess domain.

An `IllegalMoveError` is not just a failure signal — it is the message an agent reads before
retrying. Per ADR-0002, every rejection carries the complete legal move list and a human-readable
explanation of *why* the move failed, because the benchmark measures whether a model can act
correctly given complete information, not whether it can guess.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class GameError(Exception):
    """Base class for every error raised by the chess domain."""


class IllegalMoveReason(StrEnum):
    """Why a proposed move was rejected. Recorded so failure modes can be counted."""

    INVALID_NOTATION = "invalid_notation"
    """Not parseable as either algebraic or UCI notation."""

    AMBIGUOUS = "ambiguous"
    """Well-formed algebraic notation, but more than one piece could play it."""

    MISSING_PROMOTION = "missing_promotion"
    """A pawn reaching the last rank, with no piece named to promote to.

    Its own reason because the move is otherwise *right*: the pawn can go there, and the only thing
    missing is which piece it becomes. Folded into `NOT_REACHABLE` it produced a flatly false
    explanation — "the pawn on e7 cannot move to e8" — against a model that had found the move and
    left out a qualifier. Counted separately so an analysis can tell "did not know the rules" from
    "did not finish the sentence"."""

    NO_PIECE = "no_piece"
    """The origin square is empty."""

    WRONG_COLOR = "wrong_color"
    """The piece on the origin square belongs to the opponent."""

    LEAVES_KING_IN_CHECK = "leaves_king_in_check"
    """The move is otherwise valid but would expose or leave the king in check."""

    NOT_REACHABLE = "not_reachable"
    """The piece cannot reach the destination square."""

    GAME_OVER = "game_over"
    """The game has already ended."""


class IllegalMoveError(GameError):
    """A proposed move was rejected, with everything the agent needs to recover."""

    def __init__(
        self,
        *,
        move: str,
        reason: IllegalMoveReason,
        detail: str,
        fen: str,
        legal_moves_san: list[str],
    ) -> None:
        self.move = move
        self.reason = reason
        self.detail = detail
        self.fen = fen
        self.legal_moves_san = legal_moves_san
        super().__init__(f"{move!r} rejected ({reason}): {detail}")

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a tool result.

        The agent layer merges in the attempt counters — this layer has no idea how many
        retries a turn is allowed.
        """
        return {
            "ok": False,
            "error": "illegal_move",
            "move": self.move,
            "reason": str(self.reason),
            "detail": self.detail,
            "fen": self.fen,
            "legal_moves_san": self.legal_moves_san,
        }


class GameOverError(GameError):
    """An action was attempted on a game that has already finished."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

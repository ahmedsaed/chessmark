"""Match rules: applying moves and deciding when a game is over.

The referee owns the outcome. An agent can propose a move or resign; it can never declare itself
the winner. Every way a game can end is enumerated in `Termination` and recorded on the game.

**Deliberate deviation from FIDE:** threefold repetition and the fifty-move rule are *claimable*
draws under FIDE, not automatic ones. Chessmark applies them automatically, because a benchmark
cannot rely on a model noticing that it is entitled to claim a draw — without this, two weak
models shuffle pieces until the ply cap. This is recorded here so it is a decision, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chessmark.game.board import ChessBoard, LegalMove
from chessmark.game.errors import GameOverError, IllegalMoveError, IllegalMoveReason

DEFAULT_MAX_PLIES = 300


class Colour(StrEnum):
    WHITE = "white"
    BLACK = "black"

    @property
    def opponent(self) -> Colour:
        return Colour.BLACK if self is Colour.WHITE else Colour.WHITE


class GameResult(StrEnum):
    """PGN-compatible result strings."""

    WHITE_WINS = "1-0"
    BLACK_WINS = "0-1"
    DRAW = "1/2-1/2"
    ONGOING = "*"


class Termination(StrEnum):
    """Every way a Chessmark game can end (GAME-04)."""

    # Decided over the board
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    THREEFOLD_REPETITION = "threefold_repetition"
    FIFTY_MOVE_RULE = "fifty_move_rule"
    INSUFFICIENT_MATERIAL = "insufficient_material"
    RESIGNATION = "resignation"
    AGREED_DRAW = "agreed_draw"

    # Decided by the harness
    ILLEGAL_MOVE_FORFEIT = "illegal_move_forfeit"
    ERROR_FORFEIT = "error_forfeit"
    TRUNCATED = "truncated"
    """Ran out of output budget mid-reasoning, repeatedly, without ever acting."""

    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    CONTEXT_EXCEEDED = "context_exceeded"
    PLY_CAP = "ply_cap"
    ADJUDICATION = "adjudication"
    ABANDONED = "abandoned"


#: Terminations that mean a player failed rather than lost at chess. Reported separately on the
#: leaderboard — losing to a stronger model is not the same as failing to emit a legal move.
#: Endings the *harness* imposed, not the game. Chessmark ran out of budget, hit its own ply cap,
#: or could not reach a provider — none of which is anything either model did. They are the only
#: endings that may be reopened: a checkmate is final, and so is a forfeit, because both are
#: findings about a player.
RESUMABLE_TERMINATIONS = frozenset(
    {
        Termination.BUDGET_EXCEEDED,
        Termination.PLY_CAP,
        Termination.ABANDONED,
    }
)


FORFEIT_TERMINATIONS = frozenset(
    {
        Termination.ILLEGAL_MOVE_FORFEIT,
        Termination.ERROR_FORFEIT,
        Termination.TRUNCATED,
        Termination.TIMEOUT,
        Termination.BUDGET_EXCEEDED,
        Termination.CONTEXT_EXCEEDED,
    }
)


@dataclass(frozen=True, slots=True)
class Outcome:
    """How a game ended."""

    result: GameResult
    termination: Termination
    winner: Colour | None
    detail: str

    @property
    def is_forfeit(self) -> bool:
        return self.termination in FORFEIT_TERMINATIONS


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    """The result of one accepted move."""

    move: LegalMove
    ply: int
    fen_before: str
    fen_after: str
    outcome: Outcome | None
    """Set when this move ended the game."""


def _loss_for(colour: Colour) -> GameResult:
    return GameResult.BLACK_WINS if colour is Colour.WHITE else GameResult.WHITE_WINS


class Referee:
    """Applies moves to a board and decides when — and how — the game is over."""

    def __init__(self, *, start_fen: str | None = None, max_plies: int = DEFAULT_MAX_PLIES) -> None:
        self.board = ChessBoard(start_fen)
        self.max_plies = max_plies
        self._outcome: Outcome | None = None

    # ------------------------------------------------------------------ state

    @property
    def outcome(self) -> Outcome | None:
        return self._outcome

    @property
    def is_over(self) -> bool:
        return self._outcome is not None

    @property
    def result(self) -> GameResult:
        return self._outcome.result if self._outcome else GameResult.ONGOING

    @property
    def side_to_move(self) -> Colour:
        return Colour(self.board.side_to_move)

    @property
    def ply(self) -> int:
        return self.board.ply

    # ------------------------------------------------------------------ play

    def play(self, move_text: str) -> MoveOutcome:
        """Apply a move.

        Raises `IllegalMoveError` if the move is rejected — the caller decides whether that
        counts against a retry budget (AGENT-05/06). Raises `GameOverError` if the game has
        already ended.
        """
        self._require_ongoing()

        fen_before = self.board.fen
        move = self.board.push(move_text)

        return MoveOutcome(
            move=move,
            ply=self.board.ply,
            fen_before=fen_before,
            fen_after=self.board.fen,
            outcome=self._detect_natural_end(),
        )

    def resign(self, colour: Colour) -> Outcome:
        self._require_ongoing()
        return self._finish(
            Outcome(
                result=_loss_for(colour),
                termination=Termination.RESIGNATION,
                winner=colour.opponent,
                detail=f"{colour.value.capitalize()} resigned.",
            )
        )

    def agree_draw(self) -> Outcome:
        self._require_ongoing()
        return self._finish(
            Outcome(
                result=GameResult.DRAW,
                termination=Termination.AGREED_DRAW,
                winner=None,
                detail="Draw by agreement.",
            )
        )

    def forfeit(self, colour: Colour, termination: Termination, detail: str) -> Outcome:
        """End the game against a player for a harness-level failure."""
        self._require_ongoing()
        return self._finish(
            Outcome(
                result=_loss_for(colour),
                termination=termination,
                winner=colour.opponent,
                detail=detail,
            )
        )

    def adjudicate(
        self,
        result: GameResult,
        detail: str,
        *,
        termination: Termination = Termination.ADJUDICATION,
    ) -> Outcome:
        """End the game by external judgement — the ply cap, or an engine evaluation."""
        self._require_ongoing()
        winner = {
            GameResult.WHITE_WINS: Colour.WHITE,
            GameResult.BLACK_WINS: Colour.BLACK,
        }.get(result)
        return self._finish(
            Outcome(result=result, termination=termination, winner=winner, detail=detail)
        )

    # ------------------------------------------------------------------ internals

    def _require_ongoing(self) -> None:
        if self._outcome is not None:
            raise GameOverError(
                detail=f"The game is already over: {self._outcome.detail} ({self._outcome.result})"
            )

    def _finish(self, outcome: Outcome) -> Outcome:
        self._outcome = outcome
        return outcome

    def _detect_natural_end(self) -> Outcome | None:
        """Check every over-the-board terminal condition, most decisive first."""
        board = self.board

        if board.is_checkmate():
            # The side to move is the one that has been mated.
            loser = self.side_to_move
            return self._finish(
                Outcome(
                    result=_loss_for(loser),
                    termination=Termination.CHECKMATE,
                    winner=loser.opponent,
                    detail=f"{loser.opponent.value.capitalize()} mates.",
                )
            )

        if board.is_stalemate():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.STALEMATE,
                    winner=None,
                    detail=f"Stalemate — {self.side_to_move.value} has no legal move "
                    "and is not in check.",
                )
            )

        if board.is_insufficient_material():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.INSUFFICIENT_MATERIAL,
                    winner=None,
                    detail="Draw — neither side has enough material to mate.",
                )
            )

        if board.is_threefold_repetition():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.THREEFOLD_REPETITION,
                    winner=None,
                    detail="Draw — the position has occurred three times.",
                )
            )

        if board.is_fifty_move_rule():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.FIFTY_MOVE_RULE,
                    winner=None,
                    detail="Draw — fifty moves without a capture or pawn move.",
                )
            )

        if board.ply >= self.max_plies:
            # GAME-07: with no engine configured this is a draw. Phase 14 replaces it with an
            # engine adjudication at the same point.
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.PLY_CAP,
                    winner=None,
                    detail=f"Draw — the {self.max_plies}-ply cap was reached.",
                )
            )

        return None

    # ------------------------------------------------------------------ helpers

    def reject_after_retries(self, colour: Colour, error: IllegalMoveError) -> Outcome:
        """Forfeit a player that exhausted its illegal-move budget (ADR-0002)."""
        reason = (
            "an unparseable move"
            if error.reason is IllegalMoveReason.INVALID_NOTATION
            else f"an illegal move ({error.reason})"
        )
        return self.forfeit(
            colour,
            Termination.ILLEGAL_MOVE_FORFEIT,
            f"{colour.value.capitalize()} exhausted its retry budget with {reason}: "
            f"{error.move!r} — {error.detail}",
        )

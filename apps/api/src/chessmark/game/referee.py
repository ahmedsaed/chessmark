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
    """Claimed by a player, or auto-applied where a game asked for that."""

    FIFTY_MOVE_RULE = "fifty_move_rule"
    """Claimed by a player, or auto-applied where a game asked for that."""

    FIVEFOLD_REPETITION = "fivefold_repetition"
    """The hard backstop. Needs no claim from anybody (FIDE 9.6.2)."""

    SEVENTY_FIVE_MOVE_RULE = "seventy_five_move_rule"
    """The hard backstop. Needs no claim from anybody (FIDE 9.6.1)."""

    INSUFFICIENT_MATERIAL = "insufficient_material"
    RESIGNATION = "resignation"
    AGREED_DRAW = "agreed_draw"

    # Decided by the harness
    ILLEGAL_MOVE_FORFEIT = "illegal_move_forfeit"
    ERROR_FORFEIT = "error_forfeit"
    TRUNCATED = "truncated"
    """Ran out of output budget mid-reasoning, repeatedly, without ever acting.

    A **harness stop**, not a forfeit (ADR-0024): the budget that ran out is one of ours or the
    endpoint's, and neither is a property of the weights.
    """

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
        Termination.TIMEOUT,
        Termination.TRUNCATED,
    }
)


#: Endings that are findings **about a player**, and so count for ratings.
#:
#: Each one is something the model did: it played illegally six times, it answered in prose four
#: times running, it could not stop talking, it filled its own window. Reproducible, and the same
#: on any endpoint that serves the model.
#:
#: **`TRUNCATED` was here and is not a finding either (ADR-0024).** It named two different events
#: with one word. When a response stops at the ceiling *we* asked for, we ended it — ADR-0021
#: already excluded that. When it stops *below* our ask, the endpoint's own `max_completion_tokens`
#: ended it, and ADR-0021 kept that rated on the reasoning that the provider's ceiling was a
#: generous natural budget. It is not: it is a property of the endpoint, exactly like latency, and
#: the same weights on a host with a larger ceiling would not have been cut off. `laguna-s-2.1`
#: lost a game holding rook and two bishops against a lone pawn because Poolside stops at 32,768
#: output tokens and we had asked for 64,000 — a number no response from that endpoint could ever
#: reach, so the check that distinguishes the two cases could never fire.
#:
#: The clamp added alongside this (`compaction.Window.max_completion`) means we now ask for exactly
#: what an endpoint will give, so the two cases mostly collapse; this set is what makes the
#: remainder honest rather than depending on the registry being current.
#:
#: **`TIMEOUT` and `BUDGET_EXCEEDED` were here and are not findings.** Wall clock measures the
#: *provider's* latency — the same model on two endpoints got two verdicts, which is the routing
#: lottery ADR-0015 exists to remove, reappearing as a clock. One model lost a game at **ply 1
#: having never been served a single completion**. And the token ceiling counted the *prompt*,
#: which the harness re-sends every round-trip: a model that produced 5,263 tokens was forfeited
#: for "using 514,446", four replays of a 128k transcript. Five of twelve completed games in one
#: pool carried a verdict neither model had earned.
#:
#: Latency and size are still measured and published — mean latency per contestant, tokens per
#: call — as *statistics*, which is what they are. A forfeit says "it played worse", and of a slow
#: provider that claim is simply false.
FORFEIT_TERMINATIONS = frozenset(
    {
        Termination.ILLEGAL_MOVE_FORFEIT,
        Termination.ERROR_FORFEIT,
        Termination.CONTEXT_EXCEEDED,
    }
)


class DrawNotClaimableError(Exception):
    """A draw was claimed where neither claimable rule applies.

    Carries both counters, because the answer a model needs is not "no" but *how far off* it is.
    A refusal that only says no teaches nothing, and the model will simply claim again.
    """

    def __init__(self, *, repetition_count: int, halfmove_clock: int) -> None:
        self.repetition_count = repetition_count
        self.halfmove_clock = halfmove_clock
        super().__init__(
            "No draw to claim: this position has occurred "
            f"{repetition_count} time(s) (three are needed) and {halfmove_clock} half-moves "
            "have passed without a capture or pawn move (one hundred are needed)."
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

    def __init__(
        self,
        *,
        start_fen: str | None = None,
        max_plies: int = DEFAULT_MAX_PLIES,
        auto_threefold_draw: bool = False,
        auto_fifty_move_draw: bool = False,
    ) -> None:
        """`auto_*` apply a *claimable* rule without a claim, and default off.

        They were on, and unconditionally: threefold and the fifty-move rule ended a game the
        instant they were satisfiable. FIDE makes both a **claim** by the player having the move
        (9.2, 9.3) precisely because a repetition is usually good for one side and bad for the
        other, and taking the decision away from both is not neutral — a game was drawn at ply 100
        with a model a queen and a knight up, chasing the enemy king with checks it had not been
        told would repeat.

        The hard backstops (fivefold, seventy-five moves) are **not** switchable: they exist so a
        game cannot loop for ever, and they need no claim from anybody.
        """
        self.board = ChessBoard(start_fen)
        self.max_plies = max_plies
        self.auto_threefold_draw = auto_threefold_draw
        self.auto_fifty_move_draw = auto_fifty_move_draw
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

    def claim_draw(self) -> Outcome:
        """Claim a draw by threefold repetition or the fifty-move rule (FIDE 9.2, 9.3).

        Raises `DrawNotClaimableError` when neither holds, with the two counts in the message, because
        the caller is a model that has to learn something from the refusal. It is **not** an illegal
        move and must never be charged as one: a claim that does not apply is a question answered,
        not a rule broken.

        Whose turn it is does not matter here. FIDE gives the claim to the player having the move,
        but a turn in this harness *is* a player having the move — nobody else can call this — so
        enforcing it again would only add a way to get it wrong.
        """
        self._require_ongoing()
        board = self.board

        if board.is_threefold_repetition():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.THREEFOLD_REPETITION,
                    winner=None,
                    detail="Draw claimed — the position has occurred three times.",
                )
            )

        if board.is_fifty_move_rule():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.FIFTY_MOVE_RULE,
                    winner=None,
                    detail="Draw claimed — fifty moves without a capture or pawn move.",
                )
            )

        raise DrawNotClaimableError(
            repetition_count=board.repetition_count(),
            halfmove_clock=board.view().halfmove_clock,
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

        # The hard backstops first, and unconditionally: a game must not be able to loop for ever,
        # and neither of these waits for a claim (FIDE 9.6).
        if board.is_fivefold_repetition():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.FIVEFOLD_REPETITION,
                    winner=None,
                    detail="Draw — the position has occurred five times. No claim is needed.",
                )
            )

        if board.is_seventy_five_move_rule():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.SEVENTY_FIVE_MOVE_RULE,
                    winner=None,
                    detail="Draw — seventy-five moves without a capture or pawn move. "
                    "No claim is needed.",
                )
            )

        # The claimable pair, only where a game asked for them to be applied on its behalf.
        if self.auto_threefold_draw and board.is_threefold_repetition():
            return self._finish(
                Outcome(
                    result=GameResult.DRAW,
                    termination=Termination.THREEFOLD_REPETITION,
                    winner=None,
                    detail="Draw — the position has occurred three times.",
                )
            )

        if self.auto_fifty_move_draw and board.is_fifty_move_rule():
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

"""Match-level rules: who decides a game is over, and how it is recorded."""

from __future__ import annotations

import pytest

from chessmark.game import (
    Colour,
    GameOverError,
    GameResult,
    IllegalMoveError,
    Referee,
    Termination,
)


def test_a_new_game_is_ongoing() -> None:
    referee = Referee()
    assert not referee.is_over
    assert referee.outcome is None
    assert referee.result is GameResult.ONGOING
    assert referee.side_to_move is Colour.WHITE


def test_ply_counts_half_moves() -> None:
    """Phase 5 keys job idempotency off this, so it has to be exact."""
    referee = Referee()
    assert referee.ply == 0
    referee.play("e4")
    assert referee.ply == 1
    referee.play("e5")
    assert referee.ply == 2


def test_playing_a_move_reports_before_and_after() -> None:
    referee = Referee()
    outcome = referee.play("e4")

    assert outcome.move.san == "e4"
    assert outcome.ply == 1
    assert "w KQkq" in outcome.fen_before
    assert "b KQkq" in outcome.fen_after
    assert outcome.outcome is None
    assert referee.side_to_move is Colour.BLACK


# --------------------------------------------------------------------- resignation


@pytest.mark.parametrize(
    ("colour", "result", "winner"),
    [
        (Colour.WHITE, GameResult.BLACK_WINS, Colour.BLACK),
        (Colour.BLACK, GameResult.WHITE_WINS, Colour.WHITE),
    ],
)
def test_resignation_awards_the_game_to_the_opponent(
    colour: Colour, result: GameResult, winner: Colour
) -> None:
    referee = Referee()
    outcome = referee.resign(colour)

    assert outcome.result is result
    assert outcome.winner is winner
    assert outcome.termination is Termination.RESIGNATION
    assert not outcome.is_forfeit
    assert referee.is_over


def test_agreed_draw() -> None:
    referee = Referee()
    outcome = referee.agree_draw()

    assert outcome.result is GameResult.DRAW
    assert outcome.termination is Termination.AGREED_DRAW
    assert outcome.winner is None


# --------------------------------------------------------------------- forfeits


# `TIMEOUT` and `BUDGET_EXCEEDED` are deliberately absent: they measure the provider's latency and
# the harness's own prompt replay, not the play. See `tests/game/test_harness_bounds.py`.
@pytest.mark.parametrize(
    "termination",
    [
        Termination.ILLEGAL_MOVE_FORFEIT,
        Termination.ERROR_FORFEIT,
        Termination.TRUNCATED,
        Termination.CONTEXT_EXCEEDED,
    ],
)
def test_harness_failures_are_flagged_as_forfeits(termination: Termination) -> None:
    referee = Referee()
    outcome = referee.forfeit(Colour.WHITE, termination, "test")

    assert outcome.is_forfeit, "harness failures must be separable from chess losses"
    assert outcome.result is GameResult.BLACK_WINS


def test_chess_losses_are_not_forfeits() -> None:
    referee = Referee()
    assert not referee.resign(Colour.WHITE).is_forfeit


def test_exhausted_retries_forfeit_with_the_offending_move(referee: Referee | None = None) -> None:
    referee = referee or Referee()
    with pytest.raises(IllegalMoveError) as caught:
        referee.play("Qh5")

    outcome = referee.reject_after_retries(Colour.WHITE, caught.value)

    assert outcome.termination is Termination.ILLEGAL_MOVE_FORFEIT
    assert outcome.result is GameResult.BLACK_WINS
    assert "Qh5" in outcome.detail
    assert outcome.is_forfeit


# --------------------------------------------------------------------- ply cap


def test_ply_cap_draws_the_game() -> None:
    referee = Referee(max_plies=4)
    for move in ["Nf3", "Nf6", "Ng1"]:
        assert not referee.is_over
        referee.play(move)

    outcome = referee.play("Ng8")

    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.PLY_CAP
    assert outcome.outcome.result is GameResult.DRAW
    assert "4-ply cap" in outcome.outcome.detail


def test_checkmate_beats_the_ply_cap() -> None:
    # The cap falls on the same ply as the mate; the chess result must win.
    referee = Referee(max_plies=4)
    for move in ["f3", "e5", "g4"]:
        referee.play(move)
    outcome = referee.play("Qh4")

    assert outcome.outcome is not None
    assert outcome.outcome.termination is Termination.CHECKMATE


# --------------------------------------------------------------------- adjudication


def test_adjudication_records_a_winner() -> None:
    referee = Referee()
    outcome = referee.adjudicate(GameResult.WHITE_WINS, "engine evaluation +7.2")

    assert outcome.winner is Colour.WHITE
    assert outcome.termination is Termination.ADJUDICATION
    assert "7.2" in outcome.detail


def test_adjudicated_draw_has_no_winner() -> None:
    assert Referee().adjudicate(GameResult.DRAW, "equal").winner is None


# --------------------------------------------------------------------- finality


def test_a_finished_game_rejects_further_moves() -> None:
    referee = Referee()
    referee.resign(Colour.WHITE)

    with pytest.raises(GameOverError, match="already over"):
        referee.play("e4")


@pytest.mark.parametrize(
    "action",
    [
        lambda r: r.resign(Colour.WHITE),
        lambda r: r.agree_draw(),
        lambda r: r.forfeit(Colour.WHITE, Termination.TIMEOUT, "late"),
        lambda r: r.adjudicate(GameResult.DRAW, "equal"),
    ],
)
def test_a_finished_game_cannot_be_finished_twice(action: object) -> None:
    referee = Referee()
    referee.agree_draw()

    with pytest.raises(GameOverError):
        action(referee)  # type: ignore[operator]


def test_the_first_outcome_is_the_one_that_sticks() -> None:
    referee = Referee()
    referee.resign(Colour.BLACK)
    assert referee.outcome is not None
    assert referee.outcome.winner is Colour.WHITE

    with pytest.raises(GameOverError):
        referee.resign(Colour.WHITE)

    assert referee.outcome.winner is Colour.WHITE, "outcome must be immutable once set"


def test_colour_opponent() -> None:
    assert Colour.WHITE.opponent is Colour.BLACK
    assert Colour.BLACK.opponent is Colour.WHITE

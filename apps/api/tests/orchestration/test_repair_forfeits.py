"""A seat's `forfeited` flag must agree with its game's own record (invariant 1, ADR-0024).

The flag is a stored verdict and a **published** one — `bench.service` counts it into the
leaderboard's forfeits column — so a stale one is a claim about a model on a page people read.

Two ways it drifted, and both wrote a forfeit nobody earned. `turn.py` set it from the turn's
*status*, and `BUDGET_EXCEEDED` travels that way because it does end the game, while
`ratable.HARNESS_TERMINATIONS` says just as plainly that it is not a finding. And `resume` cleared
the pairing's score but not this, so two free-pool games that were budget-stopped, reopened, and
played on to a genuine checkmate and a genuine threefold draw stayed rated carrying a forfeit from
the ending that had been overturned.

The rule is derivable rather than a list of known games, which is what lets it be asserted at all:
a seat is forfeited exactly when its game ended by a forfeit **against that seat**.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from chessmark.db.enums import GameStatus
from chessmark.game import Colour, Termination

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_repair = importlib.import_module("repair_forfeits")


def judge(
    *,
    status: GameStatus = GameStatus.FINISHED,
    termination: Termination | None = Termination.ILLEGAL_MOVE_FORFEIT,
    winner: Colour | None = Colour.BLACK,
    colour: Colour = Colour.WHITE,
) -> bool:
    return _repair.should_be_forfeited(
        status=status, termination=termination, winner_colour=winner, colour=colour
    )


@pytest.mark.parametrize(
    "termination",
    [Termination.ILLEGAL_MOVE_FORFEIT, Termination.ERROR_FORFEIT],
)
def test_the_seat_a_forfeit_went_against_is_flagged(termination: Termination) -> None:
    """The benchmark's subject. Clearing these would hide what it exists to measure."""
    assert judge(termination=termination, winner=Colour.BLACK, colour=Colour.WHITE)


def test_the_winner_of_a_forfeit_is_not_flagged() -> None:
    """`referee.forfeit(colour, …)` makes the forfeiting side lose, so the flag belongs to whoever
    is not the winner. Both seats carrying it would double the leaderboard's count."""
    assert not judge(
        termination=Termination.ERROR_FORFEIT, winner=Colour.BLACK, colour=Colour.BLACK
    )


@pytest.mark.parametrize(
    "termination",
    [
        Termination.BUDGET_EXCEEDED,
        Termination.TIMEOUT,
        Termination.PLY_CAP,
        Termination.ABANDONED,
        Termination.TRUNCATED,
    ],
)
def test_a_harness_stop_is_never_a_forfeit(termination: Termination) -> None:
    """`BUDGET_EXCEEDED` is the one that actually happened: it ends the game, so it travelled as
    `TurnStatus.FORFEITED`, and the flag was written from the status rather than the ending."""
    assert not judge(termination=termination)


@pytest.mark.parametrize(
    "termination",
    [Termination.CHECKMATE, Termination.RESIGNATION, Termination.THREEFOLD_REPETITION],
)
def test_a_real_chess_result_is_never_a_forfeit(termination: Termination) -> None:
    """The two games this was written for. Both were budget-stopped, reopened, and played on to one
    of these — correct endings that the stale flag contradicted."""
    assert not judge(termination=termination)


@pytest.mark.parametrize("status", [GameStatus.ABORTED, GameStatus.RUNNING, GameStatus.PAUSED])
def test_only_a_finished_game_carries_a_verdict(status: GameStatus) -> None:
    """An aborted game is the harness giving up and is explicitly not a finding about anybody; a
    running one has not decided anything yet."""
    assert not judge(status=status, termination=Termination.ERROR_FORFEIT)


def test_a_forfeit_with_no_winner_is_left_alone() -> None:
    """Unattributable. Guessing which seat it was would be inventing the half that is missing."""
    assert not judge(termination=Termination.ERROR_FORFEIT, winner=None)


def test_a_game_with_no_termination_is_left_alone() -> None:
    assert not judge(termination=None)


def test_the_rule_is_the_forfeit_set_and_not_a_copy_of_it() -> None:
    """A termination added to `FORFEIT_TERMINATIONS` must be flagged here without anyone
    remembering to edit this script — four frozensets already drifted apart once (AGENT-17)."""
    from chessmark.game import FORFEIT_TERMINATIONS

    for termination in FORFEIT_TERMINATIONS:
        assert judge(termination=termination), f"{termination} is a forfeit and is not flagged"

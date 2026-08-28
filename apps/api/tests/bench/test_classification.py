"""Every termination is classified, and the four classifications agree (AGENT-17).

Four frozensets decide what an ending means — `FORFEIT_TERMINATIONS` and `RESUMABLE_TERMINATIONS`
in `game/referee.py`, `HARNESS_TERMINATIONS` and `RATED_TERMINATIONS` here — and nothing linked
them. So a change to one silently disagreed with the others: `TIMEOUT` was taken out of the forfeit
set and made resumable because it measured the provider rather than the player, and went on
counting toward the rating anyway, which was the whole thing that change existed to stop.

These are not style assertions. Each one names a contradiction a reader of the site would see.
"""

from __future__ import annotations

import pytest

from chessmark.bench.ratable import HARNESS_TERMINATIONS, RATED_TERMINATIONS
from chessmark.game import FORFEIT_TERMINATIONS, RESUMABLE_TERMINATIONS, Termination


def test_every_termination_is_either_rated_or_a_harness_stop() -> None:
    """A new termination that fits neither set is silently rated, because `is_ratable` tests
    membership. Adding one should fail here rather than appear on the leaderboard."""
    unclassified = set(Termination) - RATED_TERMINATIONS - HARNESS_TERMINATIONS
    assert not unclassified, f"classify these: {sorted(map(str, unclassified))}"


def test_nothing_is_both_rated_and_a_harness_stop() -> None:
    assert not RATED_TERMINATIONS & HARNESS_TERMINATIONS


@pytest.mark.parametrize("termination", sorted(RESUMABLE_TERMINATIONS, key=str))
def test_a_resumable_ending_is_never_rated(termination: Termination) -> None:
    """The two say the same thing from different directions: the harness stopped this game, so it
    can be picked up again *and* it is not a finding about a player. `TIMEOUT` was resumable and
    rated at once — a game we abandoned mid-play, scored as though somebody had lost it."""
    assert termination in HARNESS_TERMINATIONS, f"{termination} is resumable but not a harness stop"
    assert termination not in RATED_TERMINATIONS, f"{termination} is resumable and yet rated"


@pytest.mark.parametrize("termination", sorted(FORFEIT_TERMINATIONS, key=str))
def test_a_forfeit_is_rated_and_final(termination: Termination) -> None:
    """A forfeit is the benchmark's subject — agentic reliability is what is being measured — so it
    must count. And it must not be resumable: replaying a bad result until it improves is exactly
    what `resume_game.py` refuses to do."""
    assert termination in RATED_TERMINATIONS, f"{termination} is a forfeit that does not count"
    assert termination not in RESUMABLE_TERMINATIONS, f"{termination} is a forfeit and resumable"

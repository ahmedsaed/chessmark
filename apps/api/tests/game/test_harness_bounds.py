"""A harness ceiling is not a finding about a player (AGENT-17).

The distinction the ratings rest on: `FORFEIT_TERMINATIONS` is the set of endings that say
something *about the model*, and they count. Two members did not belong, and one pool made that
plain — five of twelve completed games carried a verdict neither model had earned.
"""

from __future__ import annotations

from chessmark.game import FORFEIT_TERMINATIONS, RESUMABLE_TERMINATIONS, Termination


class TestWhatCountsAsAFinding:
    def test_the_model_s_own_failures_still_count(self) -> None:
        """Each of these is something the model did, and is the same on any endpoint that serves
        it: it played illegally six times, answered in prose four times running, could not stop
        talking, filled its own window."""
        for termination in (
            Termination.ILLEGAL_MOVE_FORFEIT,
            Termination.ERROR_FORFEIT,
            Termination.TRUNCATED,
            Termination.CONTEXT_EXCEEDED,
        ):
            assert termination in FORFEIT_TERMINATIONS

    def test_wall_clock_is_not_a_finding(self) -> None:
        """It measures the *provider's* latency. The same model on two endpoints got two verdicts —
        the routing lottery ADR-0015 exists to remove, reappearing as a clock. One model lost a
        game at ply 1 having never been served a single completion."""
        assert Termination.TIMEOUT not in FORFEIT_TERMINATIONS

    def test_the_token_ceiling_is_not_a_finding(self) -> None:
        """It counted the prompt, which the harness re-sends every round-trip: a model that
        produced 5,263 tokens was forfeited for "using 514,446" — four replays of a 128k
        transcript."""
        assert Termination.BUDGET_EXCEEDED not in FORFEIT_TERMINATIONS

    def test_a_harness_stop_can_be_reopened(self) -> None:
        """Because the position is intact and nobody did anything wrong. `TIMEOUT` was not
        resumable, so a game killed by the clock could not even be picked up again."""
        for termination in (
            Termination.TIMEOUT,
            Termination.BUDGET_EXCEEDED,
            Termination.PLY_CAP,
            Termination.ABANDONED,
        ):
            assert termination in RESUMABLE_TERMINATIONS

    def test_a_real_forfeit_is_never_reopened(self) -> None:
        """Un-ending a finding would let a bad result be replayed until it improved."""
        assert not (FORFEIT_TERMINATIONS & RESUMABLE_TERMINATIONS)

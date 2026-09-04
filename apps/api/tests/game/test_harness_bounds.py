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
        it: it played illegally six times, or it answered in prose four times running."""
        for termination in (
            Termination.ILLEGAL_MOVE_FORFEIT,
            Termination.ERROR_FORFEIT,
        ):
            assert termination in FORFEIT_TERMINATIONS

    def test_filling_the_window_is_not_a_finding_either(self) -> None:
        """`CONTEXT_EXCEEDED` was here, on the reading that a model which fills its own window has
        failed. That was true while the agent had no way to shrink its history. It now folds its
        own earlier turns when the window fills (ADR-0018), so reaching the wall says the fold did
        not keep up — a fact about this harness, not about the weights (ADR-0031).

        The games that prompted it make the point: one model was cut off at its endpoint's
        undeclared 32,768-token ceiling five times in a single turn, each unfinished fragment
        appended and re-sent, until the request no longer fit. Nothing in that sequence was a
        choice the weights made.
        """
        assert Termination.CONTEXT_EXCEEDED not in FORFEIT_TERMINATIONS
        assert Termination.CONTEXT_EXCEEDED in RESUMABLE_TERMINATIONS

    def test_an_output_ceiling_is_not_a_finding(self) -> None:
        """`TRUNCATED` was here, on the reading that a model which cannot finish inside a generous
        output budget has failed. The budget is not generous or ungenerous — it belongs to the
        endpoint, and the same weights served with a larger one are not cut off. `laguna-s-2.1`
        lost a game holding rook and two bishops against a lone pawn to Poolside's 32,768-token
        response ceiling, which we had never read and were asking 64,000 against (ADR-0024)."""
        assert Termination.TRUNCATED not in FORFEIT_TERMINATIONS
        assert Termination.TRUNCATED in RESUMABLE_TERMINATIONS

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

"""A harness ceiling is not a finding about a player (AGENT-17).

The distinction the ratings rest on: `FORFEIT_TERMINATIONS` is the set of endings that say
something *about the model*, and they count. Two members did not belong, and one pool made that
plain — five of twelve completed games carried a verdict neither model had earned.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

from chessmark.agents.turn import TurnLimits, TurnResult, TurnRunner
from chessmark.db.enums import TurnStatus
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


class TestTheClockMustLeaveRoomToAsk:
    """A call that cannot succeed must not be made (AGENT-17, continued).

    `deadline_seconds` is whatever is left of the turn's clock, and `_over_budget` only stopped the
    turn once that clock had *run out* — so with 583 of 600 seconds spent it said "carry on", and
    the call it then made got a 17-second deadline. A free model's mean latency is 17-38 seconds.

    The failure surfaced as `provider call exceeded 17s`: an `LlmError`, not a rate limit, so the
    worker took the abandon path and spent five retries on it. A real game died at ply 10 with a
    message blaming the provider for our own clock.

    `_over_budget` reads nothing but `self.limits`, so it is called unbound rather than standing up
    a whole runner with a session, a game and two players.
    """

    def test_a_turn_with_no_room_left_fails_rather_than_asking(self) -> None:
        limits = TurnLimits(max_seconds=600.0, min_call_seconds=30.0)
        result = TurnResult(turn_id=uuid.uuid4(), status=TurnStatus.RUNNING)
        started = time.perf_counter() - 583.0  # 17 seconds left

        over = TurnRunner._over_budget(SimpleNamespace(limits=limits), result, started)  # type: ignore[arg-type]

        assert over
        assert result.status is TurnStatus.FAILED, "retried, never forfeited"
        assert result.outcome is None, "which is what makes the worker try again"
        assert result.error is not None
        assert "17s left" in result.error, f"it must name our clock: {result.error!r}"
        assert "30s a call needs" in result.error, f"and the floor it fell under: {result.error!r}"

    def test_a_turn_with_room_left_carries_on(self) -> None:
        limits = TurnLimits(max_seconds=600.0, min_call_seconds=30.0)
        result = TurnResult(turn_id=uuid.uuid4(), status=TurnStatus.RUNNING)
        started = time.perf_counter() - 500.0  # 100 seconds left

        assert not TurnRunner._over_budget(SimpleNamespace(limits=limits), result, started)  # type: ignore[arg-type]
        assert result.status is not TurnStatus.FAILED

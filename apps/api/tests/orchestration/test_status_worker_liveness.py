"""A worker mid-turn is not a worker that has gone (ADR-0007's reclaim, read correctly).

Redis measures a consumer's idle time from its last interaction with the group, and a worker running
a turn neither reads nor acks — so its idle time *is* how long the turn has been going. `status`
judged that against a sixty-second threshold, which is well under a normal turn:

    3,157 real turns:  median 33s, p90 224s, p99 947s
                       34.4% run longer than 60s
                        1.1% run longer than 15 minutes

So roughly a third of all turns made the status page report their worker as gone and their job as
orphaned — *"a deploy or a crash mid-turn"* — while both were working perfectly well. The queue's
own `reap_dead_consumers` has always known better: a consumer holding a delivery is never treated
as absent.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_status = importlib.import_module("status")


def held_for(seconds: int, *, holding: int = 1) -> bool:
    """Whether `status` would call a consumer alive after this long without touching the group."""
    limit = _status.WORKER_STUCK_AFTER if holding else _status.CONSUMER_DEAD_AFTER
    return seconds <= limit.total_seconds()


class TestAWorkerHoldingAJob:
    def test_two_minutes_into_a_turn_is_alive(self) -> None:
        """The report that prompted this: `71befcaa@50`, last seen 2m, called orphaned. A quarter
        of turns pass ninety seconds."""
        assert held_for(120)

    def test_so_is_a_turn_at_the_ninetieth_percentile(self) -> None:
        assert held_for(224)

    def test_and_a_long_one_short_of_the_reclaim_window(self) -> None:
        """Fifteen minutes is the queue's own patience, and nothing shorter is ours to impose in a
        status line."""
        assert held_for(890)

    def test_the_slowest_turns_genuinely_outrun_the_queue(self) -> None:
        """**Worth knowing, and not a display problem.** The 99th percentile turn takes 947 seconds
        and the reclaim window is 900, so about 1% of turns are taken back and rerun while still
        executing. That is safe — the row lock stops two workers playing one ply (ADR-0022) and the
        original rolls back whole — but the work is thrown away and paid for twice.

        Asserted so the relationship is visible rather than discovered again: if `RECLAIM_AFTER`
        ever moves, this says what it is trading against.
        """
        p99_turn_seconds = 947

        assert p99_turn_seconds > _status.RECLAIM_AFTER.total_seconds()
        assert not held_for(p99_turn_seconds)

    def test_past_the_reclaim_window_it_is_stuck(self) -> None:
        """Not "gone" — the queue takes the job back and the turn reruns, having rolled back whole.
        Worth remarking on because at that point something is genuinely wrong."""
        assert not held_for(int(_status.RECLAIM_AFTER.total_seconds()) + 1)

    def test_the_threshold_is_the_queue_s_own(self) -> None:
        """Asserted because agreeing with the queue is the whole point: a status page that calls a
        job orphaned before `XAUTOCLAIM` would reclaim it is describing a state that does not
        exist."""
        assert _status.WORKER_STUCK_AFTER == _status.RECLAIM_AFTER


class TestAWorkerHoldingNothing:
    """The shorter threshold still earns its place. A worker's name is generated per process and
    Redis keeps consumers forever, so every deploy abandons one — and a consumer with no delivery
    has no reason to be quiet."""

    def test_a_minute_of_silence_is_still_fine(self) -> None:
        assert held_for(60, holding=0)

    def test_beyond_that_it_is_a_name_left_behind(self) -> None:
        assert not held_for(61, holding=0)

    def test_it_is_far_shorter_than_the_stuck_threshold(self) -> None:
        assert _status.CONSUMER_DEAD_AFTER < _status.WORKER_STUCK_AFTER

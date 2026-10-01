"""A worker mid-turn is not a worker that has gone, and a worker that has gone says so.

Redis measures a consumer's idle time from its last interaction with the group, and a worker running
a turn neither reads nor acks — so its idle time *is* how long the turn has been going:

    3,157 real turns:  median 33s, p90 224s, p99 947s
                       34.4% run longer than 60s
                        1.1% run longer than 15 minutes

So idle time cannot say whether a worker holding a job is alive. `status` first judged it against
sixty seconds, calling a third of all turns orphaned; then against the queue's fifteen-minute
reclaim, which called a worker killed by a deploy alive for a quarter of an hour. A heartbeat
answers the question directly (`queue.ALIVE_KEY`).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_status = importlib.import_module("status")


def alive(*, beating: bool, held: int, idle: int) -> bool:
    return bool(_status.is_alive(beating=beating, held=held, idle_seconds=idle))


class TestAWorkerHoldingAJob:
    def test_a_beating_worker_is_alive_however_long_its_turn(self) -> None:
        """The p99 turn, and longer: the heartbeat runs through a turn, so its length is no
        evidence of anything."""
        for seconds in (120, 224, 947, 3600):
            assert alive(beating=True, held=1, idle=seconds)

    def test_a_worker_with_no_heartbeat_is_gone_however_recently_seen(self) -> None:
        """Game `775426ac`: its worker was stopped by a deploy, last seen five minutes before, and
        reported as playing it."""
        for seconds in (5, 300, 890):
            assert not alive(beating=False, held=1, idle=seconds)


class TestAWorkerHoldingNothing:
    """A worker's name is generated per process and Redis keeps consumers forever, so every deploy
    abandons one — and a consumer with no delivery has no reason to be quiet."""

    def test_a_beating_worker_waiting_for_work_is_alive(self) -> None:
        assert alive(beating=True, held=0, idle=5)

    def test_a_minute_of_silence_is_still_fine_without_a_heartbeat(self) -> None:
        assert alive(beating=False, held=0, idle=60)

    def test_beyond_that_it_is_a_name_left_behind(self) -> None:
        assert not alive(beating=False, held=0, idle=61)

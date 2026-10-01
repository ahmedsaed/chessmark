"""A dead worker's job is taken over at once, and never played twice.

A deploy stops every worker, and a worker stopped mid-turn leaves its job pending under a name no
process will use again. The stream's own reclaim waits fifteen minutes, because it cannot tell a
worker that is gone from one deep in a slow turn — so game `775426ac` sat on "Solar thinking"
with nothing in its log for a quarter of an hour after a deploy. A heartbeat tells them apart.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.scripted import plays
from chessmark.db.repositories import get_game
from chessmark.orchestration.queue import ALIVE_KEY, TurnQueue
from chessmark.orchestration.worker import ADVANCED, IN_FLIGHT
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration


async def _held_by(queue: TurnQueue, consumer: str) -> None:
    """The fixture's first job, delivered to `consumer` and never acknowledged."""
    delivered = await queue.consume(consumer, block_ms=200)
    assert delivered, "the fixture must hand it a job"


async def test_a_job_whose_worker_has_no_heartbeat_is_taken_over_at_once(
    queue: TurnQueue, game: Fixture
) -> None:
    await _held_by(queue, "worker-stopped-by-a-deploy")

    taken = await queue.reclaim_orphaned("worker-new", min_idle_ms=0)

    assert [d.job.game_id for d in taken] == [game.game.id]
    assert taken[0].redelivered


async def test_a_live_workers_job_is_never_taken_however_long_its_turn(
    queue: TurnQueue, game: Fixture
) -> None:
    """The whole point of the heartbeat: a slow turn is not a dead worker."""
    await queue.beat("worker-busy")
    await _held_by(queue, "worker-busy")

    assert await queue.reclaim_orphaned("worker-new", min_idle_ms=0) == []
    assert await queue.pending_count() == 1


async def test_a_worker_that_stops_cleanly_gives_up_its_job_without_waiting(
    queue: TurnQueue, game: Fixture
) -> None:
    await queue.beat("worker-leaving")
    await _held_by(queue, "worker-leaving")
    await queue.forget("worker-leaving")

    assert await queue.reclaim_orphaned("worker-new", min_idle_ms=0)


async def test_a_job_delivered_a_moment_ago_is_left_alone(queue: TurnQueue, game: Fixture) -> None:
    """At the default threshold, an entry younger than the heartbeat's expiry is not touched —
    its holder may simply not have beaten yet."""
    await _held_by(queue, "worker-just-started")
    assert await queue.reclaim_orphaned("worker-new") == []


async def test_of_two_workers_reclaiming_one_job_only_one_gets_it(
    queue: TurnQueue, game: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`XCLAIM`'s minimum idle is the lock: the first claim resets the entry's idle time, and the
    second finds it too fresh to take.

    **Both are made to read the pending list before either claims**, with a barrier on the
    heartbeat lookup. Left to the scheduler, one finishes before the other starts, sees the entry
    already re-owned, and the test passes without the claim ever being contested.
    """
    await _held_by(queue, "worker-gone")
    await asyncio.sleep(0.06)

    barrier = asyncio.Barrier(2)
    exists = queue.redis.exists

    async def both_have_looked(*keys: Any) -> Any:
        found = await exists(*keys)
        await barrier.wait()
        return found

    monkeypatch.setattr(queue.redis, "exists", both_have_looked)
    first, second = await asyncio.gather(
        queue.reclaim_orphaned("worker-a", min_idle_ms=50),
        queue.reclaim_orphaned("worker-b", min_idle_ms=50),
    )

    assert len(first) + len(second) == 1


async def test_a_live_worker_mistaken_for_dead_still_plays_nothing_twice(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: TurnQueue,
    game: Fixture,
    make_worker: Any,
) -> None:
    """The second guarantee. Should a live worker's heartbeat ever lapse mid-turn, its job is
    claimed — and the claimer's turn is refused by the game's row lock the owner still holds."""
    await _held_by(queue, "worker-owner")
    (taken,) = await queue.reclaim_orphaned("worker-claimer", min_idle_ms=0)

    async with sessionmaker() as owner, owner.begin():
        await get_game(owner, game.game.id, claim=True)  # the owner's turn, still running
        handled = await make_worker(plays(["e4"])).handle(taken.job)

    assert handled.outcome == IN_FLIGHT
    assert handled.result is None, "it never reached a provider"


async def test_the_new_worker_plays_the_orphaned_turn(
    queue: TurnQueue, game: Fixture, make_worker: Any
) -> None:
    await _held_by(queue, "worker-stopped-by-a-deploy")
    (taken,) = await queue.reclaim_orphaned("worker-new", min_idle_ms=0)

    handled = await make_worker(plays(["e4"])).process(taken)

    assert handled.outcome == ADVANCED
    assert await queue.pending_count() == 0


async def test_a_running_worker_beats_and_says_when_it_stops(
    queue: TurnQueue, make_worker: Any
) -> None:
    worker = make_worker(plays([]))
    key = ALIVE_KEY.format(consumer=worker.consumer)

    running = asyncio.create_task(worker.run_forever())
    for _ in range(50):
        if await queue.redis.exists(key):
            break
        await asyncio.sleep(0.02)
    assert await queue.redis.exists(key), "alive before it takes anything"

    worker.stop()
    await asyncio.wait_for(running, timeout=5)
    assert not await queue.redis.exists(key), "a clean stop frees its jobs at once"


async def test_a_running_worker_never_takes_a_live_workers_job_however_long_it_runs(
    queue: TurnQueue, game: Fixture, make_worker: Any
) -> None:
    """The slowest 1% of turns outran the stream's fifteen-minute reclaim and were rerun while
    still being played. The worker no longer asks that question at all."""
    await queue.beat("worker-in-a-long-turn")
    await _held_by(queue, "worker-in-a-long-turn")
    claimed: list[str] = []
    original = queue.redis.xautoclaim

    async def record(*args: Any, **kwargs: Any) -> Any:
        claimed.append("xautoclaim")
        return await original(*args, **kwargs)

    queue.redis.xautoclaim = record  # type: ignore[method-assign]
    worker = make_worker(plays([]))
    running = asyncio.create_task(worker.run_forever(orphans_every=1))
    await asyncio.sleep(0.3)
    worker.stop()
    await asyncio.wait_for(running, timeout=5)

    assert claimed == []
    assert await queue.pending_count() == 1

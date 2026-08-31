"""Consumer names do not outlive their processes (OPS-21).

A worker's name is `worker-{uuid4}`, generated per **process**, and Redis keeps a consumer in a
group forever — only `XGROUP DELCONSUMER` removes it. Fifty-one names had accumulated over a few
days of deploys, and `status` could not tell a reader which three of them were the containers
actually running.

The two safeguards are the whole design, and neither is sufficient alone: a name holding a delivery
is never removed (deleting it discards that consumer's pending entries, which would silently drop a
turn), and the idle threshold is three hundred poll intervals, so nothing running can reach it.
"""

from __future__ import annotations

import pytest

from chessmark.orchestration.queue import AdvanceTurn, TurnQueue
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration


async def _names(queue: TurnQueue) -> set[str]:
    """Consumer names, whichever way the client is decoding.

    The worker's Redis decodes nothing and the operator scripts decode everything, so both shapes
    reach this code — which is exactly the difference that made the first version of the reaper a
    silent no-op.
    """
    consumers = await queue.redis.xinfo_consumers(queue.stream, queue.group)
    names = set()
    for consumer in consumers:
        raw = consumer.get("name", consumer.get(b"name", b""))
        names.add(raw.decode() if isinstance(raw, bytes) else str(raw))
    return names


async def test_an_idle_name_is_forgotten(queue: TurnQueue) -> None:
    """The deploy-leftover case: a process that consumed once and went away.

    Deliberately without the `game` fixture, which enqueues a first turn — a consumer that picked
    that up would hold a delivery and be spared, which is the *next* test.
    """
    await queue.consume("worker-gone", block_ms=50)
    assert "worker-gone" in await _names(queue)

    # `-1` rather than `0`: the rule is "idle *longer* than this", and a consumer created in the
    # same millisecond reports an idle of 0, so a threshold of 0 would spare it and the test would
    # be measuring the clock rather than the behaviour.
    reaped = await queue.reap_consumers(idle_ms=-1)

    assert "worker-gone" in reaped
    assert "worker-gone" not in await _names(queue)


async def test_a_name_holding_a_delivery_is_kept(queue: TurnQueue, game: Fixture) -> None:
    """**The safeguard that matters.** `XGROUP DELCONSUMER` discards pending entries, so removing
    this name would drop the turn instead of leaving it for `XAUTOCLAIM` to reclaim."""
    await queue.enqueue(AdvanceTurn(game_id=game.game.id, expected_ply=0))
    delivered = await queue.consume("worker-busy", block_ms=200)
    assert delivered, "the fixture must actually hand it a job"

    reaped = await queue.reap_consumers(idle_ms=-1)

    assert "worker-busy" not in reaped
    assert "worker-busy" in await _names(queue)


async def test_the_delivery_survives_the_sweep(queue: TurnQueue, game: Fixture) -> None:
    """Nothing is lost: the entry stays pending and can still be reclaimed."""
    await queue.enqueue(AdvanceTurn(game_id=game.game.id, expected_ply=0))
    await queue.consume("worker-busy", block_ms=200)

    await queue.reap_consumers(idle_ms=-1)

    assert await queue.pending_count() == 1
    assert await queue.reclaim_stalled("worker-fresh", min_idle_ms=0)


async def test_a_live_worker_is_not_reaped(queue: TurnQueue) -> None:
    """The default threshold is ten minutes against a two-second poll, so a worker that has just
    checked in cannot be mistaken for a dead one."""
    await queue.consume("worker-live", block_ms=50)

    assert await queue.reap_consumers() == []
    assert "worker-live" in await _names(queue)


async def test_reaping_an_empty_group_is_harmless(queue: TurnQueue) -> None:
    assert await queue.reap_consumers(idle_ms=-1) == []

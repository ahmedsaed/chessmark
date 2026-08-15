"""Non-functional targets: fanout across processes, and latency (NFR-01, NFR-02).

These are measurements, not micro-benchmarks. They run in-process against a real database and a
real Redis, so the absolute numbers are optimistic compared with production — no network hop, no
TLS. They are here to catch an *order of magnitude* regression: an accidental N+1 query or a
synchronous call on the hot path, not a ten-millisecond drift.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.api.deps import get_redis, get_session
from chessmark.db.enums import EventType
from chessmark.main import create_app
from tests.support import Fixture, both_sides, run_next

pytestmark = pytest.mark.integration

#: NFR-01. Deliberately loose: this measures a real query path, and a target so tight that
#: ordinary variance trips it would get ignored rather than fixed.
P95_BUDGET_MS = 200.0

#: NFR-02, measured from ply commit to the frame arriving at a connected client.
SSE_P95_BUDGET_MS = 500.0

#: NFR-01 says "50 RPS", which is an *offered rate*, not 50 simultaneous requests. Firing 50 at
#: once and timing each from a common start measures queueing — every request reports the time to
#: drain the whole batch — which is throughput wearing a latency costume. Pacing them 20ms apart
#: measures what the requirement actually asks about.
TARGET_RPS = 50
REQUEST_COUNT = 100

#: Enough concurrent requests to force the pool to open the connections the run will need.
WARMUP_REQUESTS = 20


def build_app(sessionmaker: Any, redis: Any) -> FastAPI:
    """A second, independent app instance — a stand-in for another API process."""
    app = create_app()

    async def _session() -> Any:
        async with sessionmaker() as session:
            yield session

    async def _redis() -> Any:
        return redis

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    return app


# ====================================================================== fanout


async def test_two_api_processes_both_receive_one_workers_events(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any, redis: Any
) -> None:
    """Exit criterion, and the claim the whole API tier rests on.

    A spectator connected to instance A must see events produced by a worker talking to instance
    B. That is the entire reason Redis is the fanout bus rather than each process polling its own
    database (ADR-0008). Two independent app instances here stand in for two processes.
    """
    apps = [build_app(sessionmaker, redis) for _ in range(2)]
    received: list[list[int]] = [[], []]

    async def watch(index: int, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream("GET", f"/games/{game.game.id}/stream") as response,
        ):
            async for line in response.aiter_lines():
                if line.startswith("id:"):
                    received[index].append(int(line.split(":", 1)[1].strip()))
                elif line.strip() == f"event: {EventType.GAME_ENDED}":
                    return

    watchers = [asyncio.create_task(watch(i, app)) for i, app in enumerate(apps)]
    await asyncio.sleep(0.6)  # both subscriptions attach

    worker = make_worker(scripted(step(tool_call("resign"))), publish=True)
    await run_next(worker, game.queue)

    for watcher in watchers:
        try:
            await asyncio.wait_for(watcher, timeout=15)
        except TimeoutError:
            watcher.cancel()

    assert received[0], "instance A saw nothing"
    assert received[1], "instance B saw nothing"
    assert received[0] == received[1], (
        "the two instances disagree about what happened — the fanout is not actually shared"
    )


# ====================================================================== latency


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]


async def test_read_endpoints_stay_within_the_latency_budget(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any, redis: Any
) -> None:
    """NFR-01: p95 under 200 ms on non-LLM endpoints, at an offered load of 50 RPS."""
    worker = make_worker(both_sides(["e4", "Nf3"], ["e5", "Nc6"]))
    for _ in range(4):
        if await run_next(worker, game.queue) is None:
            break

    app = build_app(sessionmaker, redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Warm the *pool*, not just the query plans. Opening a Postgres connection costs ~900ms
        # here (WSL2 through Docker), so a single warm-up request leaves the pool holding one
        # connection and any brief concurrency pays 900ms to open another. Those were the tail:
        # measured cold, checkout median was 920ms; warm, 28ms. In production the pool is warm and
        # this cost is paid once at startup, so warming it is what makes the number mean what
        # NFR-01 says it means.
        await asyncio.gather(
            *(client.get(f"/games/{game.game.id}") for _ in range(WARMUP_REQUESTS))
        )

        async def timed(path: str, delay: float) -> float:
            await asyncio.sleep(delay)
            started = time.perf_counter()
            response = await client.get(path)
            assert response.status_code == 200
            return (time.perf_counter() - started) * 1000

        interval = 1.0 / TARGET_RPS
        for path in (
            "/games",
            f"/games/{game.game.id}",
            f"/games/{game.game.id}/plies",
        ):
            samples = list(
                await asyncio.gather(
                    *(timed(path, index * interval) for index in range(REQUEST_COUNT))
                )
            )
            p95 = _p95(samples)
            assert p95 < P95_BUDGET_MS, (
                f"{path}: p95 {p95:.1f}ms over the {P95_BUDGET_MS:.0f}ms budget "
                f"(median {statistics.median(samples):.1f}ms)"
            )


async def test_sse_delivery_is_prompt_after_a_ply_commits(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any, redis: Any
) -> None:
    """NFR-02: a move reaches a connected spectator within 500 ms of committing."""
    app = build_app(sessionmaker, redis)
    transport = ASGITransport(app=app)
    arrivals: list[float] = []

    async def watch() -> None:
        async with (
            AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream("GET", f"/games/{game.game.id}/stream") as response,
        ):
            async for line in response.aiter_lines():
                if line.strip().startswith("event: move_made"):
                    arrivals.append(time.perf_counter())
                elif line.strip() == f"event: {EventType.GAME_ENDED}":
                    return

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0.6)

    worker = make_worker(both_sides(["f3", "g4"], ["e5", "Qh4"]), publish=True)
    commits: list[float] = []
    for _ in range(4):
        if await run_next(worker, game.queue) is None:
            break
        commits.append(time.perf_counter())

    try:
        await asyncio.wait_for(watcher, timeout=15)
    except TimeoutError:
        watcher.cancel()

    assert arrivals, "no move reached the spectator"

    # Pair each arrival with the commit that produced it.
    deltas = [
        (arrival - commit) * 1000
        for commit, arrival in zip(commits, arrivals, strict=False)
        if arrival >= commit
    ]
    assert deltas, "arrivals did not line up with commits"

    p95 = _p95(deltas)
    assert p95 < SSE_P95_BUDGET_MS, (
        f"SSE delivery p95 {p95:.1f}ms over the {SSE_P95_BUDGET_MS:.0f}ms budget"
    )

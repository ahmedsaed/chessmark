"""One worker's events reach every API process (NFR-11, ADR-0008).

The claim the whole API tier rests on: a spectator connected to instance A sees events produced by
a worker talking to instance B. Two independent app instances stand in for two processes.

**This file used to assert latency too, and no longer does.** Two tests held a wall-clock p95
against a fixed budget — read-endpoint latency (NFR-01) and SSE delivery after a ply commits
(NFR-02) — in-process, on whatever machine happened to be running them, so what they measured was
the runner. The record of that is in ROADMAP's Phase 6 notes:
the test was wrong twice in different ways, and *"the first green run was luck — the criterion was
ticked on an unstable measurement before anyone had checked it twice."* It went on failing that
way: a run with a **5.7 ms median** and a **329 ms p95** turned a frontend-only pull request red,
on an endpoint that pull request did not touch.

A timing assertion that fails on a busy runner and passes on a quiet one teaches people to rerun
CI, which is worse than having no assertion at all — the next real regression is rerun away too.
What those tests were standing in for is an N+1 or a synchronous call on the hot path, and that is
caught deterministically by the query-count tests the read path is now held to
(`CLAUDE.md`, *Performance*): counting statements passes or fails identically on every machine.
The budgets stay in REQUIREMENTS as NFR-01 and NFR-02, measured under load in Phase 17 rather than
guessed at in a unit suite; ROADMAP's *Known gaps* records that they have no automated check.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.api.deps import get_redis, get_session
from chessmark.db.enums import EventType
from chessmark.main import create_app
from tests.support import Fixture, run_next

pytestmark = pytest.mark.integration


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

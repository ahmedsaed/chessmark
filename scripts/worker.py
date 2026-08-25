#!/usr/bin/env python3
"""A standalone turn worker.

    make worker

Consumes `advance_turn` jobs until interrupted. Run as many as you like — jobs are idempotent
(ADR-0007), so workers never need to coordinate. Also periodically reconciles games that stalled
because their job was lost entirely rather than merely dropped by a dead worker.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from redis.asyncio import Redis  # noqa: E402

from chessmark.agents.llm import LlmGateway  # noqa: E402
from chessmark.agents.pricing import PricingTable  # noqa: E402
from chessmark.core.budget import GlobalBudget  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.orchestration import TurnQueue, TurnWorker, reconcile  # noqa: E402

log = logging.getLogger("chessmark.worker")


async def reconcile_loop(sessionmaker: Any, queue: TurnQueue, *, every: float = 300.0) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            report = await reconcile(sessionmaker, queue)
            if report.requeued:
                log.warning("reconciler: %s", report)
        except Exception:
            log.exception("reconciler failed")


async def main() -> int:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )

    # Environment first, then `.env` via settings — the file is where the key lives for everyone
    # working on this project, and reading only `os.environ` meant the worker refused to start
    # while every other entry point found the key.
    api_key = os.environ.get("OPENROUTER_API_KEY") or settings.openrouter_api_key
    if not api_key:
        print(
            "OPENROUTER_API_KEY is not set in the environment or .env",
            file=sys.stderr,
        )
        return 2

    redis: Redis[Any] = Redis.from_url(str(settings.redis_url))
    queue = TurnQueue(redis)
    sessionmaker = get_sessionmaker()

    # Pricing comes from `model_registry`, refreshed by `make seed-models`. Read once at start-up:
    # it is a fallback for calls where OpenRouter does not report its own cost, and a worker that
    # re-read it per turn would query the same rows thousands of times a game.
    async with sessionmaker() as session:
        pricing = await PricingTable.from_registry(session)
    log.info("pricing loaded for %d models", len(pricing))

    # Layer 1 of ADR-0011. This worker is what serves games started from the web UI, so without a
    # budget it is a way to spend money with no daily ceiling — the same hole `make play` had.
    budget = GlobalBudget(redis, daily_limit_usd=Decimal(str(settings.global_daily_usd_budget)))

    worker = TurnWorker(
        sessionmaker=sessionmaker,
        queue=queue,
        gateway=LlmGateway(
            api_key=api_key,
            pricing=pricing,
        ),
        redis=redis,
        budget=budget,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    log.info("worker %s started", worker.consumer)
    reconciler = asyncio.create_task(reconcile_loop(sessionmaker, queue))
    try:
        await worker.run_forever()
    finally:
        reconciler.cancel()
        await redis.aclose()
        await dispose_engine()

    log.info("worker %s stopped", worker.consumer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

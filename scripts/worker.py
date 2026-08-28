#!/usr/bin/env python3
"""A standalone turn worker.

    make worker              # real models, real money
    make worker ARGS=--scripted   # a scripted opponent, no key, no spend

Consumes `advance_turn` jobs until interrupted. Run as many as you like — jobs are idempotent
(ADR-0007), so workers never need to coordinate. Also periodically reconciles games that stalled
because their job was lost entirely rather than merely dropped by a dead worker.
"""

from __future__ import annotations

import argparse
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
from chessmark.agents.scripted import responsive  # noqa: E402
from chessmark.core.budget import FreeTierBudget, GlobalBudget  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.core.cooldown import ProviderCooldown  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.orchestration import TurnQueue, TurnWorker, reconcile  # noqa: E402
from chessmark.orchestration.reconciler import SingleFlight  # noqa: E402

log = logging.getLogger("chessmark.worker")

#: What the scripted opponent "thinks" while it moves.
#:
#: It exists so invariant 8 is testable: reasoning must be withheld while a game is live and
#: readable once it is over. A scripted model that never reasons would let that gate be deleted
#: without a single test noticing.
SCRIPTED_REASONING = "Taking the first move the board offers. I am scripted; there is no plan."


async def reconcile_loop(
    sessionmaker: Any, queue: TurnQueue, *, redis: Any = None, every: float = 60.0
) -> None:
    """Rescue stalled games, and resume paused ones.

    Every minute rather than every five. The sweep is two indexed queries and was always cheap;
    what changed is that it now also decides when a paused game comes back, and the shortest
    cooldown rung is sixty seconds — at a five-minute tick that rung would have meant five.
    """
    while True:
        await asyncio.sleep(every)
        try:
            # One worker at a time. Every worker runs this loop, and several sweeping together
            # would each see the same free concurrency slot and each fill it.
            async with SingleFlight(redis) as mine:
                if not mine:
                    continue
                report = await reconcile(sessionmaker, queue)
            if report.requeued or report.resumed:
                log.warning("reconciler: %s", report)
        except Exception:
            log.exception("reconciler failed")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scripted",
        action="store_true",
        help=(
            "Play model turns with a scripted opponent that reads the board — no API key, no "
            "spend, deterministic. This is what the browser suite runs against."
        ),
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )

    # Environment first, then `.env` via settings — the file is where the key lives for everyone
    # working on this project, and reading only `os.environ` meant the worker refused to start
    # while every other entry point found the key.
    api_key = os.environ.get("OPENROUTER_API_KEY") or settings.openrouter_api_key
    if not api_key and not args.scripted:
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

    # The free tier is bounded by a request count, not by money, and nothing reports it back to
    # us. Counting our own attempts here is the only way anything downstream can know how much of
    # the day's allowance is left — including the tournament runner, which refuses to start a new
    # game once it is spent.
    free_tier = FreeTierBudget(redis)

    # What is remembered between games about an endpoint that refused. Shared through Redis rather
    # than held per process, because the point is that the *next* game — and the tournament
    # matchmaker in another container entirely — knows what this one just learned.
    cooldown = ProviderCooldown(redis)

    async def count_free_requests(model: str) -> None:
        if model.endswith(":free"):
            await free_tier.record()

    worker = TurnWorker(
        sessionmaker=sessionmaker,
        queue=queue,
        gateway=(
            # The provider is the only thing replaced: normalisation, costing, persistence and the
            # retry loop all run for real, so a game played this way exercises the same code a paid
            # one does. `completion_fn` takes precedence, so the key is never read.
            LlmGateway(completion_fn=responsive(reasoning=SCRIPTED_REASONING), pricing=pricing)
            if args.scripted
            else LlmGateway(api_key=api_key, pricing=pricing, on_attempt=count_free_requests)
        ),
        redis=redis,
        budget=budget,
        cooldown=cooldown,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    mode = " (scripted — no spend)" if args.scripted else ""
    log.info("worker %s started%s", worker.consumer, mode)
    reconciler = asyncio.create_task(reconcile_loop(sessionmaker, queue, redis=redis))
    try:
        await worker.run_forever()
    finally:
        reconciler.cancel()
        await redis.aclose()
        await dispose_engine()

    log.info("worker %s stopped", worker.consumer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

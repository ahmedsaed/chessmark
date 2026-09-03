#!/usr/bin/env python3
"""Refresh the model catalogue end to end, unattended (OPS-09, OPS-23).

    make refresh-catalogue                 # once, now
    make refresh-catalogue ARGS="--every 12"   # every twelve hours, forever

One command, because the two halves are useless apart: `seed-models` registers what exists and
`refresh-endpoints` decides what is *playable*, and a model with no endpoint rows has no
contestants and cannot be picked. Running only the first is how this environment ended up
offering two models out of three hundred.

Built to be scheduled rather than remembered. Prices set the spend caps **and** what users are
charged in credits (ADR-0016), so a stale catalogue is a wrong cap and a wrong price — which is
exactly what a committed snapshot did for months without anything noticing.

**`--every` is the schedule, and it lives here rather than in a crontab.** A timer on the host is
one more thing that exists nowhere in this repository, that a rebuilt server loses silently, and
that nobody discovers is missing until a price is six months old. With the interval in the script,
the stack's own `catalogue` service is the schedule and `docker compose ps` is how you check it.

**A refresh that cannot reach OpenRouter changes nothing.** It exits non-zero with the registry
untouched, so a one-shot run fails loudly and a scheduler retries. Under `--every` a failed pass is
logged and the loop waits for the next one instead: a container that exits on a transient blip is
restarted by Docker into the same blip, and this file's own compose stack has watched that happen
to the tournament runner.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import httpx  # noqa: E402

from chessmark.agents.registry import (  # noqa: E402
    fetch_catalogue,
    fetch_endpoints,
    playable_models,
    sync_endpoints,
    sync_model_registry,
)
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


async def refresh(*, skip_endpoints: bool = False) -> int:
    """One pass. Returns a process exit code: 0 for a refresh that happened, 1 for one that did not.

    The engine is disposed at the end of every pass, not only at the end of the process. Under
    `--every` this thing sleeps for hours between passes, and ten pooled connections held open
    across a night are ten connections a database restart turns into surprises.
    """
    settings = get_settings()
    started = time.monotonic()
    headers = (
        {"Authorization": f"Bearer {settings.openrouter_api_key}"}
        if settings.openrouter_api_key
        else {}
    )

    try:
        async with httpx.AsyncClient(timeout=60, headers=headers) as client:
            entries = await fetch_catalogue(client, min_context=settings.min_context_tokens)
            print(f"catalogue : {len(entries)} playable models")
            if not entries:
                print("empty catalogue — refusing to touch the registry", file=sys.stderr)
                return 1

            async with session_scope() as session:
                report = await sync_model_registry(session, entries, disable_missing=True)
                print(f"registry  : {report}")

            if skip_endpoints:
                print("endpoints : skipped")
                return 0

            # One request per model, so this is the slow half — and the reason this belongs on a
            # schedule rather than in a deploy step.
            async with session_scope() as session:
                models = await playable_models(session)
                failures: Counter[str] = Counter()
                total = 0
                for model in models:
                    try:
                        found = await fetch_endpoints(client, model.openrouter_id)
                    except Exception as exc:  # noqa: BLE001 — one bad model must not stop the sweep
                        failures[type(exc).__name__] += 1
                        continue
                    total += await sync_endpoints(session, model, found)

            print(f"endpoints : {total} across {len(models)} models")
            if failures:
                print(f"            {sum(failures.values())} failed: {dict(failures)}")

        print(f"done in {time.monotonic() - started:.0f}s")
        return 0
    finally:
        await dispose_engine()


async def forever(*, every_hours: float, skip_endpoints: bool = False) -> int:
    """Refresh on an interval until something stops the process.

    **The first pass runs immediately.** A stack that has just come up should not serve six-month
    old prices for twelve hours because that is when the timer happens to fire, and the sweep costs
    nothing but OpenRouter's own metadata endpoints — no inference, so nothing against the daily
    free allowance.

    **Nothing here raises.** A pass that fails — OpenRouter down, the network gone, Postgres
    restarting — is logged and the loop waits. Exiting would hand the failure to Docker's restart
    policy, which would put the process straight back into the same failure, and a tight retry loop
    against a provider that is already unhappy is the last thing that helps.
    """
    seconds = every_hours * 3600
    while True:
        stamp = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
        print(f"--- refresh at {stamp}", flush=True)
        try:
            if await refresh(skip_endpoints=skip_endpoints) != 0:
                print("refresh changed nothing; will try again next interval", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — a scheduled sweep must outlive one bad night
            print(f"refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        print(f"next refresh in {every_hours:g}h", flush=True)
        await asyncio.sleep(seconds)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-endpoints",
        action="store_true",
        help="register models but do not sweep their endpoints (fast; leaves the picker stale)",
    )
    parser.add_argument(
        "--every",
        type=float,
        metavar="HOURS",
        help="refresh now and then every HOURS, forever. Omit to run once and exit",
    )
    args = parser.parse_args()

    if args.every is not None:
        if args.every <= 0:
            # A zero or negative interval is a busy loop against somebody else's API. Refused
            # rather than clamped: whoever typed it meant something, and it was not this.
            print("--every must be a positive number of hours", file=sys.stderr)
            return 2
        return await forever(every_hours=args.every, skip_endpoints=args.skip_endpoints)

    return await refresh(skip_endpoints=args.skip_endpoints)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

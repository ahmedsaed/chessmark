#!/usr/bin/env python3
"""Refresh the model catalogue end to end, unattended (OPS-09).

    make refresh-catalogue

One command, because the two halves are useless apart: `seed-models` registers what exists and
`refresh-endpoints` decides what is *playable*, and a model with no endpoint rows has no
contestants and cannot be picked. Running only the first is how this environment ended up
offering two models out of three hundred.

Built to be scheduled rather than remembered. Prices set the spend caps **and** what users are
charged in credits (ADR-0016), so a stale catalogue is a wrong cap and a wrong price — which is
exactly what a committed snapshot did for months without anything noticing.

**A refresh that cannot reach OpenRouter changes nothing.** It exits non-zero with the registry
untouched, so a scheduler retries rather than a half-written catalogue going live.
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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-endpoints",
        action="store_true",
        help="register models but do not sweep their endpoints (fast; leaves the picker stale)",
    )
    args = parser.parse_args()

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

            if args.skip_endpoints:
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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

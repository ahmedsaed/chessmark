#!/usr/bin/env python3
"""Load the playable-model catalogue from OpenRouter into `model_registry`.

    make seed-models                 # every tool-capable model
    make seed-models ARGS=--free     # free tool-capable models only
    make seed-models ARGS=--disable-missing

Idempotent — safe to run on every deploy.

**This reads the live catalogue, not a file.** There used to be a committed `seeds/models.json`
written by a second script, and it went stale without anything noticing: 239 models against 419
live, missing every frontier model, because the writer defaulted to free models only. Pricing here
backs the ADR-0011 budget caps, so a stale price is a wrong cap.

Needs the network. A run that cannot reach OpenRouter fails and changes nothing; the rows already
in Postgres keep serving, so a live system is unaffected by a failed refresh.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import httpx  # noqa: E402

from chessmark.agents.registry import (  # noqa: E402
    fetch_catalogue,
    playable_models,
    sync_model_registry,
)
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--free",
        action="store_true",
        help="register only free models (they cannot finish a real game, but cost nothing)",
    )
    parser.add_argument(
        "--disable-missing",
        action="store_true",
        help="disable registry rows absent from the catalogue (they are never deleted, so games "
        "that used them stay readable)",
    )
    args = parser.parse_args()

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            entries = await fetch_catalogue(client, free_only=args.free)

        print(f"openrouter: {len(entries)} tool-capable models")
        if not entries:
            print("nothing to sync — refusing to touch the registry", file=sys.stderr)
            return 1

        async with session_scope() as session:
            report = await sync_model_registry(
                session, entries, disable_missing=args.disable_missing
            )
            playable = await playable_models(session)

        print(f"registry  : {report}")
        print(f"playable  : {len(playable)} tool-capable models registered")
        print("\nRun `make refresh-endpoints` next — a model has no contestants, and so cannot")
        print("be picked in the UI, until its endpoints are known.")
        return 0
    finally:
        # Inside the loop that opened it. Disposing from a second `asyncio.run` finds the first
        # loop already closed and raises over the top of whatever actually happened here.
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

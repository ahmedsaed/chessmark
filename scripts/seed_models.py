#!/usr/bin/env python3
"""Load `seeds/models.json` into the model_registry table.

Idempotent — safe to run on every deploy.

    make seed-models
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from chessmark.agents.registry import load_seed, playable_models, sync_model_registry  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--disable-missing",
        action="store_true",
        help="disable registry rows absent from the seed (they are never deleted, so games "
        "that used them stay readable)",
    )
    args = parser.parse_args()

    entries = load_seed()
    print(f"seed file: {len(entries)} models")

    async with session_scope() as session:
        report = await sync_model_registry(
            session, entries, disable_missing=args.disable_missing
        )
        playable = await playable_models(session, free_only=True)

    print(f"registry : {report}")
    print(f"playable : {len(playable)} free, tool-capable models")
    for model in playable[:5]:
        print(f"   {model.openrouter_id:<48} ctx={model.context_length}")
    if len(playable) > 5:
        print(f"   … and {len(playable) - 5} more")

    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

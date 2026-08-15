#!/usr/bin/env python3
"""Refresh which providers serve each registered model, and at what precision.

    make refresh-endpoints

OpenRouter fans one model id out across many providers at different quantizations —
`deepseek-v4-flash` has 18, from fp8 down to fp4, some declaring nothing. A chat response names the
provider that served it but never its precision, so without this table a finished game cannot say
what it was actually played at.

Nothing is deleted: an endpoint that disappears is marked inactive, because a game that already
ran must stay explicable.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import httpx  # noqa: E402

from chessmark.agents.registry import (  # noqa: E402
    fetch_endpoints,
    playable_models,
    sync_endpoints,
)
from chessmark.agents.routing import DEFAULT_QUANTIZATIONS  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


async def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    totals: Counter[str] = Counter()

    async with (
        httpx.AsyncClient(timeout=60, headers=headers) as client,
        session_scope() as session,
    ):
        models = await playable_models(session)
        print(f"refreshing endpoints for {len(models)} models\n")

        for model in models:
            try:
                endpoints = await fetch_endpoints(client, model.openrouter_id)
            except Exception as exc:  # noqa: BLE001 - one bad model must not stop the sweep
                print(f"  ✗ {model.openrouter_id}: {type(exc).__name__}")
                continue

            count = await sync_endpoints(session, model, endpoints)
            quants = Counter(str(e.get("quantization") or "unknown") for e in endpoints)
            totals.update(quants)

            excluded = sorted(q for q in quants if q not in DEFAULT_QUANTIZATIONS)
            flag = f"  ⚠ excluded by default: {', '.join(excluded)}" if excluded else ""
            print(f"  {model.openrouter_id:<46} {count:>2} endpoints  {dict(quants)}{flag}")

    print(f"\nquantizations seen across all models: {dict(totals.most_common())}")
    print(f"allowed by default: {', '.join(DEFAULT_QUANTIZATIONS)}")

    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

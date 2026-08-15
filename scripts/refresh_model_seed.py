#!/usr/bin/env python3
"""Refresh the model registry seed from OpenRouter's public model list.

Chessmark can only use models that support tool calling — the entire agent runtime
is tool-mediated (AGENT-01), so a model without `tools` in its supported parameters
is unplayable, free or not.

Usage:
    python scripts/refresh_model_seed.py            # free tool-capable models only
    python scripts/refresh_model_seed.py --all      # every tool-capable model
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

MODELS_URL = "https://openrouter.ai/api/v1/models"
SEED_PATH = Path(__file__).resolve().parent.parent / "apps" / "api" / "seeds" / "models.json"


def fetch_models() -> list[dict[str, Any]]:
    with urllib.request.urlopen(MODELS_URL, timeout=60) as response:  # noqa: S310
        payload: dict[str, Any] = json.load(response)
    models: list[dict[str, Any]] = payload["data"]
    return models


def to_seed_entry(model: dict[str, Any]) -> dict[str, Any]:
    pricing = model.get("pricing") or {}
    supported = model.get("supported_parameters") or []
    model_id: str = model["id"]

    return {
        "openrouter_id": model_id,
        "display_name": model.get("name") or model_id,
        "context_length": model.get("context_length"),
        "prompt_usd_per_token": float(pricing.get("prompt") or 0.0),
        "completion_usd_per_token": float(pricing.get("completion") or 0.0),
        "supports_reasoning": "reasoning" in supported,
        "is_free": model_id.endswith(":free"),
        "enabled": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true", help="include every tool-capable model, paid ones too"
    )
    parser.add_argument(
        "--max-prompt-price",
        type=float,
        default=None,
        help="include paid models at or below this USD per prompt token (e.g. 1e-6 for $1/M)",
    )
    args = parser.parse_args()

    models = fetch_models()
    tool_capable = [m for m in models if "tools" in (m.get("supported_parameters") or [])]

    free = [m for m in tool_capable if m["id"].endswith(":free")]

    if args.all:
        selected = tool_capable
    elif args.max_prompt_price is not None:
        # Free models plus paid ones under the cap. Benchmarking anything real needs paid models:
        # the free tier is too slow and too verbose to finish a game.
        def prompt_price(model: dict[str, Any]) -> float:
            try:
                return float((model.get("pricing") or {}).get("prompt") or 0.0)
            except (TypeError, ValueError):
                return float("inf")

        affordable = [
            m
            for m in tool_capable
            if not m["id"].endswith(":free") and 0 < prompt_price(m) <= args.max_prompt_price
        ]
        selected = free + affordable
    else:
        selected = free

    entries = sorted(
        (to_seed_entry(m) for m in selected),
        key=lambda e: (not e["is_free"], -(e["context_length"] or 0)),
    )

    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

    print(f"{len(models)} models on OpenRouter", file=sys.stderr)
    print(f"{len(tool_capable)} support tool calling", file=sys.stderr)
    print(f"wrote {len(entries)} entries to {SEED_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

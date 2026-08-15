#!/usr/bin/env python3
"""Record real provider responses as test fixtures.

The test suite replays these; it never calls a provider. Recording is deliberate and manual —
run this when a provider changes its response shape, not on every test run:

    make record-llm

Requires OPENROUTER_API_KEY. Uses free models only, so a recording session costs nothing.
Responses are redacted before they are written, so a fixture can never carry a credential.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
FIXTURES = API_ROOT / "tests" / "fixtures" / "llm"

sys.path.insert(0, str(API_ROOT / "src"))

from chessmark.agents.llm import LlmGateway  # noqa: E402
from chessmark.agents.redaction import contains_secret, redact  # noqa: E402

MOVE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "make_move",
        "description": "Play a chess move in the current position.",
        "parameters": {
            "type": "object",
            "properties": {
                "move": {
                    "type": "string",
                    "description": "The move in standard algebraic notation, e.g. e4 or Nf3.",
                }
            },
            "required": ["move"],
        },
    },
}

OPENING_MESSAGES: list[dict[str, Any]] = [
    {
        "role": "system",
        "content": (
            "You are playing chess as White. Use the make_move tool to play. "
            "Do not reply in prose."
        ),
    },
    {
        "role": "user",
        "content": (
            "Position (FEN): rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1\n"
            "Legal moves: a3, a4, b3, b4, c3, c4, d3, d4, e3, e4, f3, f4, g3, g4, h3, h4, "
            "Na3, Nc3, Nf3, Nh3\n"
            "Make your move."
        ),
    },
]

#: Free, tool-capable models covering different provider families.
TARGETS: list[tuple[str, str]] = [
    ("openai_style_tool_call", "openai/gpt-oss-20b:free"),
    ("nvidia_reasoning_tool_call", "nvidia/nemotron-nano-9b-v2:free"),
    ("google_gemma_tool_call", "google/gemma-4-31b-it:free"),
]


async def record(name: str, model: str, *, gateway: LlmGateway) -> bool:
    print(f"→ {name}: calling {model} …", flush=True)
    try:
        completion = await gateway.complete(
            model=model,
            messages=OPENING_MESSAGES,
            tools=[MOVE_TOOL],
            max_tokens=2000,
        )
    except Exception as exc:  # noqa: BLE001 - a failed recording is reported, not fatal
        print(f"  ✗ {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return False

    payload = {
        "name": name,
        "model": model,
        "source": "live",
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "note": "Recorded from a free OpenRouter model. Replayed by tests; never re-fetched.",
        "request": redact(completion.request),
        "response": completion.response,
    }

    if contains_secret(payload):
        print("  ✗ refusing to write: payload still looks like it contains a credential")
        return False

    destination = FIXTURES / f"{name}.json"
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tool = completion.tool_calls[0].name if completion.tool_calls else "none"
    print(
        f"  ✓ {destination.name}  tool={tool}  "
        f"tokens={completion.usage.prompt}+{completion.usage.completion} "
        f"cached={completion.usage.cached}  cost=${completion.cost_usd} "
        f"({completion.cost_source})  {completion.latency_ms}ms",
        flush=True,
    )
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="record just this fixture name")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2

    FIXTURES.mkdir(parents=True, exist_ok=True)
    gateway = LlmGateway(api_key=api_key)

    targets = [t for t in TARGETS if not args.only or t[0] == args.only]
    results = [await record(name, model, gateway=gateway) for name, model in targets]

    recorded = sum(results)
    print(f"\n{recorded}/{len(results)} fixtures recorded into {FIXTURES.relative_to(REPO_ROOT)}")
    return 0 if recorded else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

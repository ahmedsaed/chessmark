#!/usr/bin/env python3
"""One real end-to-end call, to prove the gateway works against the live provider.

Run manually — `make smoke-llm` — never in CI. The test suite replays recorded fixtures and
never spends anything; this exists so that "it works offline" and "it works for real" are
separate claims we can each check.

Also reports the prompt-cache hit rate on a second call sharing the first's prefix (NFR-06).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from chessmark.agents.llm import LlmGateway  # noqa: E402
from chessmark.agents.pricing import PricingTable  # noqa: E402
from chessmark.agents.redaction import contains_secret  # noqa: E402
from chessmark.db.session import get_sessionmaker  # noqa: E402

DEFAULT_MODEL = "openai/gpt-oss-20b:free"

MOVE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "make_move",
        "description": "Play a chess move.",
        "parameters": {
            "type": "object",
            "properties": {"move": {"type": "string"}},
            "required": ["move"],
        },
    },
}

# A deliberately long, stable prefix — prompt caching only pays off when the prefix repeats
# byte-for-byte (ADR-0003), so the second call below reuses this exactly.
SYSTEM_PROMPT = (
    "You are a chess engine playing White in a benchmark game. Use the make_move tool to play. "
    "Never reply in prose. Consider development, king safety, and material before moving. "
) * 20


async def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2

    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL

    async with get_sessionmaker()() as session:
        pricing = await PricingTable.from_registry(session)
    gateway = LlmGateway(api_key=api_key, pricing=pricing)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Position (FEN): rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1\n"
                "Legal moves: a3, a4, b3, b4, c3, c4, d3, d4, e3, e4, f3, f4, g3, g4, h3, h4, "
                "Na3, Nc3, Nf3, Nh3\nMake your move."
            ),
        },
    ]

    print(f"model: {model}\n")

    for label in ("first call", "second call (same prefix)"):
        result = await gateway.complete(
            model=model, messages=messages, tools=[MOVE_TOOL], max_tokens=2000
        )

        call = result.tool_calls[0] if result.tool_calls else None
        print(f"── {label}")
        print(f"   move       : {call.arguments.get('move') if call else '(no tool call)'}")
        print(f"   tokens     : {result.usage.prompt} prompt + {result.usage.completion} out")
        print(f"   reasoning  : {result.usage.reasoning} tokens")
        print(f"   cached     : {result.usage.cached} ({result.usage.cache_hit_rate:.0%})")
        print(f"   cost       : ${result.cost_usd} ({result.cost_source})")
        print(f"   latency    : {result.latency_ms} ms")
        print(f"   attempts   : {result.attempts}")
        print(f"   finish     : {result.finish_reason}")
        print(f"   no secrets : {not contains_secret(result.request | result.response)}")
        print()

    print(
        "Note: OpenRouter's free tier generally does not report cached tokens. A 0% hit rate "
        "here is expected and does not indicate a bug — NFR-06 needs a caching-capable model to "
        "verify for real."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

#!/usr/bin/env python
"""Does each endpoint keep its reasoning through a streamed call? (ADR-0036)

The runtime guard catches an endpoint that drops its thinking, at a cost of one call. This answers
the same question ahead of time, for a whole pool, so an operator can see the state of the world
rather than read it out of worker logs after the fact.

Two calls per model — one whole, one streamed — and the comparison that matters:

    whole   reasoning_tokens=223  text=878 chars
    stream  reasoning_tokens=232  text=903 chars   107 fragments, first at 0.85s

Reasoning tokens on both and text on both is a healthy endpoint. **Tokens but no text on the
streamed call is the LiteLLM bug** — the provider billed us for thinking it then did not hand
over ([#21386](https://github.com/BerriAI/litellm/issues/21386)). Tokens and no text on *both* is
a model that hides its reasoning, which is not a fault and is not ours to fix.

Spends money, so it lives here rather than in the test suite (`scripts/` holds anything that
does). On free models it spends nothing.

    ./chessmark verify-streaming
    make verify-streaming ARGS="vendor/model-a vendor/model-b"
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.agents.llm import LlmGateway  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.models import ModelEndpoint, ModelRegistry  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, AMBER = "\033[31m", "\033[32m", "\033[33m"

#: Short and cheap, and it has to actually provoke thinking — a model asked something trivial may
#: legitimately return no reasoning at all, which would read as the bug.
PROMPT = [{"role": "user", "content": "Think it through briefly, then answer: what is 17 * 23?"}]


async def probe(model: str, *, stream: bool, api_key: str) -> dict[str, Any]:
    fragments = 0
    first: float | None = None
    started = time.perf_counter()

    async def on_token(kind: str, text: str) -> None:
        nonlocal fragments, first
        if kind != "reasoning":
            return
        fragments += 1
        if first is None:
            first = time.perf_counter() - started

    gateway = LlmGateway(api_key=api_key, stream=stream, timeout=180)
    completion = await gateway.complete(
        model=model, messages=PROMPT, max_tokens=800, on_token=on_token
    )
    return {
        "tokens": completion.usage.reasoning,
        "characters": len(completion.reasoning or ""),
        "fragments": fragments,
        "first": first,
        "elapsed": time.perf_counter() - started,
    }


async def models_to_check(argv: list[str]) -> list[str]:
    if argv:
        return argv
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        rows = await session.scalars(
            sa.select(ModelRegistry.openrouter_id)
            .where(ModelRegistry.enabled.is_(True), ModelRegistry.supports_reasoning.is_(True))
            .where(
                ModelRegistry.id.in_(
                    sa.select(ModelEndpoint.model_id).where(ModelEndpoint.is_active.is_(True))
                )
            )
            .order_by(ModelRegistry.openrouter_id)
        )
        return list(rows)


async def main(argv: list[str]) -> int:
    api_key = get_settings().openrouter_api_key
    if not api_key:
        print("OPENROUTER_API_KEY is not set; nothing to verify.", file=sys.stderr)
        return 1

    models = await models_to_check(argv)
    if not models:
        print("no reasoning-capable models with an active endpoint", file=sys.stderr)
        return 1

    print(f"{BOLD}Verifying {len(models)} model(s){OFF}\n")
    broken: list[str] = []

    for model in models:
        print(f"{BOLD}{model}{OFF}")
        results: dict[str, dict[str, Any]] = {}
        for mode, streaming in (("whole", False), ("stream", True)):
            try:
                results[mode] = await probe(model, stream=streaming, api_key=api_key)
            except Exception as error:  # one bad model must not stop the sweep
                print(f"  {mode:6} {RED}failed{OFF} {type(error).__name__}: {str(error)[:100]}")
        if len(results) != 2:
            print()
            continue

        for mode, r in results.items():
            lead = f"first at {r['first']:.2f}s" if r["first"] is not None else "no fragments"
            print(
                f"  {mode:6} reasoning_tokens={r['tokens']:5}  text={r['characters']:6} chars  "
                f"{r['fragments']:4} fragments  {lead}  {DIM}({r['elapsed']:.1f}s){OFF}"
            )

        whole, streamed = results["whole"], results["stream"]
        if streamed["tokens"] > 0 and streamed["characters"] == 0:
            if whole["characters"] > 0:
                # The exact contradiction ADR-0036 keys on: billed for thinking, handed none.
                print(f"  {RED}✗ loses its reasoning when streamed{OFF}")
                broken.append(model)
            else:
                print(f"  {AMBER}• hides its reasoning either way — not a fault{OFF}")
        elif streamed["characters"] > 0:
            print(f"  {GREEN}✓ reasoning survives streaming{OFF}")
        else:
            print(f"  {DIM}• does not reason on this prompt; nothing to lose{OFF}")
        print()

    if broken:
        print(f"{RED}{len(broken)} endpoint(s) lose reasoning when streamed:{OFF}")
        for model in broken:
            print(f"  {model}")
        print(
            f"\n{DIM}The worker detects this itself and falls back after one call (ADR-0036).{OFF}"
        )
    else:
        print(f"{GREEN}Every model checked keeps its reasoning through a streamed call.{OFF}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(sys.argv[1:])))
    finally:
        asyncio.run(dispose_engine())

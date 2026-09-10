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

**Free models only unless you say otherwise, and that default is load-bearing.** Written without
it, this checked every reasoning-capable model with an active endpoint: 194 models, 388 calls, 179
of them paid — with `openai/gpt-5.4-pro` at $180 per million output tokens on an 800-token budget,
that is about $0.29 for one model and tens of dollars for the sweep. A verification tool that can
cost that much by default is one nobody dares run, which defeats the point of having it.

    ./chessmark verify-streaming                       # the free pool — costs nothing
    ./chessmark verify-streaming vendor/model-a        # exactly these, whatever they cost
    ./chessmark verify-streaming --paid                # everything, after printing the bill

Not something to run on a schedule. The worker detects the same fault itself at a cost of one call
(ADR-0036); this is for looking at a whole pool at once — after a catalogue refresh, or when a
model's reasoning stops appearing on the site.
"""

from __future__ import annotations

import asyncio
import sys
import time
from decimal import Decimal
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
        model=model, messages=PROMPT, max_tokens=MAX_TOKENS, on_token=on_token
    )
    return {
        "tokens": completion.usage.reasoning,
        "characters": len(completion.reasoning or ""),
        "fragments": fragments,
        "first": first,
        "elapsed": time.perf_counter() - started,
    }


#: What one model costs at worst: two calls, each allowed `MAX_TOKENS` of output, every one of
#: them billed at the completion rate. Prompt tokens are a rounding error against that.
MAX_TOKENS = 800


async def models_to_check(argv: list[str]) -> tuple[list[str], Decimal]:
    """The models to probe and what they could cost, at worst.

    Named slugs are taken as given — asking for a model by name is asking for it. With no
    arguments the free pool is checked and nothing else, because the alternative is a command that
    quietly spends tens of dollars the first time anyone tries it.
    """
    wanted = [arg for arg in argv if not arg.startswith("-")]
    include_paid = "--paid" in argv

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        query = (
            sa.select(ModelRegistry)
            .where(ModelRegistry.enabled.is_(True), ModelRegistry.supports_reasoning.is_(True))
            .where(
                ModelRegistry.id.in_(
                    sa.select(ModelEndpoint.model_id).where(ModelEndpoint.is_active.is_(True))
                )
            )
            .order_by(ModelRegistry.openrouter_id)
        )
        if wanted:
            query = query.where(ModelRegistry.openrouter_id.in_(wanted))
        elif not include_paid:
            query = query.where(ModelRegistry.is_free.is_(True))

        rows = list(await session.scalars(query))

    # Two calls a model, each able to spend the whole output budget.
    worst = sum((row.completion_usd_per_token or Decimal(0)) * MAX_TOKENS * 2 for row in rows)
    return [row.openrouter_id for row in rows], Decimal(worst)


async def main(argv: list[str]) -> int:
    api_key = get_settings().openrouter_api_key
    if not api_key:
        print("OPENROUTER_API_KEY is not set; nothing to verify.", file=sys.stderr)
        return 1

    models, worst = await models_to_check(argv)
    if not models:
        print("no reasoning-capable models with an active endpoint", file=sys.stderr)
        return 1

    # Said before the first call, not after the last one. The number is a ceiling — a model that
    # answers in forty tokens is not billed for eight hundred — but a ceiling is the only figure
    # worth showing to someone deciding whether to press enter.
    bill = "nothing — free models only" if worst == 0 else f"up to ${worst:.2f}"
    print(
        f"{BOLD}Verifying {len(models)} model(s){OFF} {DIM}· {len(models) * 2} calls · {bill}{OFF}"
    )
    if worst > 0:
        print(f"{DIM}  ({MAX_TOKENS} output tokens allowed per call, twice per model){OFF}")
    print()
    broken: list[str] = []
    checked = 0
    unreachable: list[str] = []

    for model in models:
        print(f"{BOLD}{model}{OFF}")
        results: dict[str, dict[str, Any]] = {}
        for mode, streaming in (("whole", False), ("stream", True)):
            try:
                results[mode] = await probe(model, stream=streaming, api_key=api_key)
            except Exception as error:  # one bad model must not stop the sweep
                print(f"  {mode:6} {RED}failed{OFF} {type(error).__name__}: {str(error)[:100]}")
        if len(results) != 2:
            # **Not a pass.** The free tier's daily allowance runs out, models are withdrawn, and
            # providers 404 — a model we could not reach is one we know nothing about, and counting
            # it as healthy is how a verification tool ends up certifying an empty run.
            unreachable.append(model)
            print()
            continue

        checked += 1

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
    elif checked == 0:
        # The whole run told us nothing, and has to say so: silence must not read as assent.
        print(f"{AMBER}Nothing was verified: all {len(models)} model(s) were unreachable.{OFF}")
        print(f"{DIM}Free-tier allowance spent, or the endpoints are down. Try again later.{OFF}")
        await dispose_engine()
        return 1
    else:
        print(
            f"{GREEN}{checked} of {len(models)} model(s) keep their reasoning when streamed.{OFF}"
        )

    if unreachable:
        # Said even on a good run: a sweep that reached two models out of fifteen has not cleared
        # the other thirteen, and the line above is only about what it actually saw.
        print(f"\n{AMBER}{len(unreachable)} not reached, so not verified:{OFF}")
        for model in unreachable:
            print(f"  {DIM}{model}{OFF}")

    # In *this* loop: a second `asyncio.run` in a `finally` is a second event loop, and asyncpg's
    # connections belong to the first one — closing them from the other raises at exit.
    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

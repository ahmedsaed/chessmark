#!/usr/bin/env python3
"""Seed the fixtures the browser suite reads, and print their ids as JSON.

    make seed-e2e

The suite asserts against a **finished** game — replay scrubs it ply by ply and opens the raw
payload behind a turn — and a finished game cannot be produced by the browser: it needs a whole
game already in the database.

That game is played here through the *real* orchestration path with a scripted provider, exactly
as `make play ARGS=--scripted` does: the real queue, the real worker, one transaction per turn. A
hand-written fixture row would be faster and would prove nothing, because the shapes the replay
reads — `game_events`, `turns`, `llm_calls` — would then be shapes this script invented rather
than shapes the runtime writes.

Idempotent: an existing seeded game is reused rather than duplicated, so running the suite twice
does not fill the database with Scholar's Mates.
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sqlalchemy as sa  # noqa: E402
from play_game import scripted_players  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from chessmark.agents.llm import LlmGateway  # noqa: E402
from chessmark.agents.registry import sync_model_registry  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.enums import GameStatus  # noqa: E402
from chessmark.db.models import Game, ModelEndpoint, ModelRegistry, Player  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.orchestration import (  # noqa: E402
    Seat,
    TurnQueue,
    TurnWorker,
    create_match,
    start_match,
)

#: Marks a seat as belonging to the suite. The white seat of a seeded game carries it, which is
#: both how an existing fixture is found again and how a person reading the lobby can tell a
#: seeded game from one they played.
WHITE = "e2e/white"
BLACK = "e2e/black"

#: Enough of a catalogue for the picker and the model pages to have something to show.
#:
#: Only used when the registry is **empty**, which is CI. A developer's database has the real
#: catalogue in it (`make seed-models`) and this leaves it alone — the suite must never quietly
#: replace 256 real models with three invented ones.
#:
#: Hand-written rather than fetched, because a browser suite that calls OpenRouter to decide what
#: to render is a suite that fails when OpenRouter is slow.
FALLBACK_CATALOGUE = [
    {
        "openrouter_id": "google/gemini-3.7-flash",
        "display_name": "Google: Gemini 3.7 Flash",
        "context_length": 1_048_576,
        "supports_tools": True,
        "prompt_usd_per_token": "0.0000003",
        "completion_usd_per_token": "0.0000025",
    },
    {
        "openrouter_id": "anthropic/claude-sonnet-5",
        "display_name": "Anthropic: Claude Sonnet 5",
        "context_length": 200_000,
        "supports_tools": True,
        "prompt_usd_per_token": "0.000003",
        "completion_usd_per_token": "0.000015",
    },
    {
        "openrouter_id": "moonshotai/kimi-k2.5",
        "display_name": "MoonshotAI: Kimi K2.5",
        "context_length": 256_000,
        "supports_tools": True,
        "prompt_usd_per_token": "0.0000006",
        "completion_usd_per_token": "0.0000025",
    },
]


async def ensure_catalogue(session: Any) -> int:
    """Give an empty registry something to list. Returns how many models exist afterwards.

    A model is only *playable* if it also has an active tool-capable endpoint (ADR-0015), so the
    endpoint rows matter as much as the registry ones: without them `/models` answers `[]` and the
    picker offers nothing to sit down against.
    """
    already = await session.scalar(sa.select(sa.func.count(ModelRegistry.id)))
    if already:
        return int(already)

    await sync_model_registry(session, FALLBACK_CATALOGUE)
    await session.flush()

    for entry in FALLBACK_CATALOGUE:
        model_id = await session.scalar(
            sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == entry["openrouter_id"])
        )
        session.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name="e2e",
                quantization="fp8",
                context_length=entry["context_length"],
                supports_tools=True,
                is_active=True,
            )
        )
    await session.commit()
    return len(FALLBACK_CATALOGUE)


async def existing_replay_game(session: Any) -> str | None:
    """A finished seeded game, if one is already here."""
    game_id = await session.scalar(
        sa.select(Game.id)
        .join(Player, Player.game_id == Game.id)
        .where(
            Game.status == GameStatus.FINISHED,
            Player.colour == "white",
            Player.sampling["model"].astext == WHITE,
        )
        .order_by(Game.created_at.desc())
        .limit(1)
    )
    return str(game_id) if game_id else None


async def play_scripted_game() -> str:
    """Scholar's Mate, played for real against a scripted provider. Returns the game id."""
    settings = get_settings()
    redis: Redis[Any] = Redis.from_url(str(settings.redis_url))
    queue = TurnQueue(redis)
    await queue.ensure_group()
    sessionmaker = get_sessionmaker()

    try:
        async with sessionmaker() as session:
            match = await create_match(
                session,
                white=Seat(display_name="Scripted White", model=WHITE),
                black=Seat(display_name="Scripted Black", model=BLACK),
                is_ranked=False,
                # Not zero. A scripted provider reports no cost, but a cap of $0 is *already*
                # exceeded at ply 0 — the first seed run produced a 0-ply `budget_exceeded` draw
                # rather than a game. The cap has to be a number the game can stay under.
                max_usd=Decimal("1"),
                max_plies=20,
            )
            job = await start_match(session, queue, game_id=match.game.id)
            await session.commit()
            game_id = match.game.id

        await queue.enqueue(job)

        # No budget: a scripted provider reports no cost, so there is nothing to meter. Passing a
        # real `GlobalBudget` here would let a day's spending elsewhere fail the suite.
        worker = TurnWorker(
            sessionmaker=sessionmaker,
            queue=queue,
            gateway=LlmGateway(completion_fn=scripted_players()),
            redis=redis,
            consumer="seed-e2e",
        )

        # Drains until the queue goes quiet, which for a scripted game is the mate on move four.
        # Bounded so a bug cannot spin here forever: Scholar's Mate is seven plies and each
        # enqueues one job.
        for _ in range(40):
            deliveries = await queue.consume("seed-e2e", block_ms=2000)
            if not deliveries:
                break
            for delivery in deliveries:
                await worker.process(delivery)

        return str(game_id)
    finally:
        await redis.aclose()


async def main() -> int:
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            models = await ensure_catalogue(session)

        async with sessionmaker() as session:
            game_id = await existing_replay_game(session)

        if game_id is None:
            game_id = await play_scripted_game()

        async with sessionmaker() as session:
            game = await session.get(Game, game_id)
            if game is None or game.status != GameStatus.FINISHED:
                print(
                    f"seeded game {game_id} did not finish "
                    f"(status {game.status if game else 'missing'})",
                    file=sys.stderr,
                )
                return 1
            fixtures = {
                "replayGame": str(game.id),
                "result": str(game.result),
                "plyCount": game.ply_count,
                "models": models,
            }

        print(json.dumps(fixtures, indent=2))
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

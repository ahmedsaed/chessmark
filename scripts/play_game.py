#!/usr/bin/env python3
"""Play a full game and watch it happen.

    make play                       # two free models, live
    make play ARGS="--scripted"     # no API key, no spend — proves the pipeline
    make play ARGS="--white openai/gpt-oss-20b:free --black nvidia/nemotron-nano-9b-v2:free"

Runs the real orchestration path — the real queue, the real worker, one transaction per turn —
with the worker in this process so the whole game is visible in one terminal.
`scripts/worker.py` runs the same worker standalone.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from chessmark.agents.llm import LlmGateway  # noqa: E402
from chessmark.agents.pricing import PricingTable  # noqa: E402
from chessmark.agents.routing import DEFAULT_QUANTIZATIONS, ProviderRouting  # noqa: E402
from chessmark.agents.scripted import step, tool_call  # noqa: E402
from chessmark.core.budget import GlobalBudget  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.models import Game, GameEvent, Ply  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import ChessBoard  # noqa: E402
from chessmark.orchestration import (  # noqa: E402
    Seat,
    TurnQueue,
    TurnWorker,
    create_match,
    start_match,
)

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
AMBER, CYAN, RED, GREEN = "\033[38;5;179m", "\033[38;5;73m", "\033[38;5;167m", "\033[38;5;108m"

#: A short scripted game (the Scholar's Mate) so `--scripted` demonstrates the whole pipeline,
#: including a real terminal state, with no provider and no spend.
SCRIPTED_WHITE = ["e4", "Bc4", "Qh5", "Qxf7"]
SCRIPTED_BLACK = ["e5", "Nc6", "Nf6"]

# Deliberately exercises all four conversation registers, because a scripted game is how the UI is
# developed and a script that only calls tools leaves most of the panel untested. Real models split
# these differently — Gemini writes `content` and no `reasoning`, DeepSeek the exact reverse — so
# White here is given prose and Black is given thinking.
SCRIPTED_WHITE_TALK = [
    "I will play the Italian Game. A classic for a reason.",
    "Bishop to c4, eyeing f7 — the weakest square in your camp.",
    "Queen to h5. You may want to look at f7 now.",
    "Mate on f7. Good game.",
]
SCRIPTED_BLACK_THOUGHT = [
    "White played e4. The classical reply is e5, fighting for the centre immediately.",
    "Developing the knight to c6 defends e5 and adds a piece to the centre.",
    "Nf6 develops with tempo. I should be watching f7, but I want the piece out.",
]


def scripted_players() -> Any:
    """Both sides of a scripted game, with prose, reasoning, and trash talk.

    Chooses a side from the system prompt at the head of the transcript, the same way
    `agents.scripted.alternating` does — the worker calls the gateway once per turn without saying
    whose turn it is.
    """
    white = iter(
        [
            step(
                tool_call("get_legal_moves"),
                tool_call("say", message=talk),
                tool_call("make_move", move=move),
                content=talk,
            )
            for move, talk in zip(SCRIPTED_WHITE, SCRIPTED_WHITE_TALK, strict=True)
        ]
    )
    black = iter(
        [
            step(
                tool_call("get_board"),
                tool_call("make_move", move=move),
                reasoning=thought,
            )
            for move, thought in zip(SCRIPTED_BLACK, SCRIPTED_BLACK_THOUGHT, strict=True)
        ]
    )

    async def _complete(**kwargs: Any) -> Any:
        messages = kwargs.get("messages") or [{}]
        system = str(messages[0].get("content", ""))
        source = white if "as white" in system.lower() else black
        return next(source)

    return _complete


def indent(text: str, prefix: str = "     ") -> str:
    return "\n".join(f"{DIM}{prefix}{line}{OFF}" for line in text.splitlines())


def render(event: GameEvent, board: ChessBoard) -> bool:
    """Print one event. Returns True when the game has ended."""
    kind = str(event.type)
    payload: dict[str, Any] = event.payload or {}

    if kind == "turn_started":
        print(
            f"\n{DIM}ply {payload.get('ply')} · {payload.get('colour')} · "
            f"{payload.get('model', '')}{OFF}"
        )

    elif kind == "output":
        text = str(payload.get("content", "")).replace("\n", " ").strip()
        if text:
            print(f"  {BOLD}>{OFF} {text[:150]}{'…' if len(text) > 150 else ''}")

    elif kind == "thinking":
        text = str(payload.get("reasoning", "")).replace("\n", " ").strip()
        if text:
            print(f"  {CYAN}▏{OFF} {DIM}{text[:150]}{'…' if len(text) > 150 else ''}{OFF}")

    elif kind == "tool_called":
        print(f"  {DIM}▸ {payload.get('tool')}(){OFF}")

    elif kind == "illegal_attempt":
        print(
            f"  {RED}✗ {payload.get('move')} — {str(payload.get('detail', ''))[:88]}"
            f" (attempt {payload.get('attempt')}){OFF}"
        )

    elif kind == "move_made":
        board.push(str(payload.get("san")))
        print(f"  {BOLD}{AMBER}{payload.get('san')}{OFF}")
        print(indent(board.ascii()))

    elif kind == "message_sent":
        tint = AMBER if payload.get("colour") == "white" else CYAN
        print(f"  {tint}💬 {payload.get('content')}{OFF}")

    elif kind == "game_ended":
        print(f"\n{GREEN}══ {payload.get('result')} · {payload.get('termination')}{OFF}")
        print(f"   {payload.get('detail')}")
        return True

    return False


async def summarise(sessionmaker: Any, game_id: Any) -> None:
    """Cost per ply and the cached-token ratio — the numbers Phase 5 is meant to produce."""
    async with sessionmaker() as session:
        game = await session.get(Game, game_id)
        plies = list(
            await session.scalars(
                sa.select(Ply).where(Ply.game_id == game_id).order_by(Ply.ply_number)
            )
        )
        totals = (
            await session.execute(
                sa.text(
                    "SELECT COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(cached_tokens),0),"
                    " COALESCE(SUM(completion_tokens),0), COUNT(*)"
                    " FROM llm_calls WHERE game_id = :g"
                ),
                {"g": game_id},
            )
        ).one()

    if game is None:
        return

    prompt, cached, completion, calls = totals
    hit_rate = (cached / prompt * 100) if prompt else 0.0

    print(f"\n{DIM}{'─' * 62}{OFF}")
    print(f"  result      : {game.result}  ({game.termination})")
    print(f"  plies       : {len(plies)}")
    print(f"  llm calls   : {calls}")
    print(f"  tokens      : {prompt} prompt + {completion} out")
    print(f"  cached      : {cached}  ({hit_rate:.1f}% hit rate)")
    print(f"  total cost  : ${game.total_cost_usd:.6f}")
    if plies:
        print(f"  cost / ply  : ${game.total_cost_usd / len(plies):.6f}")
    print(f"\n  {' '.join(p.san for p in plies)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--white", default="nvidia/nemotron-nano-9b-v2:free")
    parser.add_argument("--black", default="openai/gpt-oss-20b:free")
    parser.add_argument("--scripted", action="store_true", help="no provider, no spend")
    parser.add_argument("--max-usd", type=Decimal, default=Decimal("0.50"))
    parser.add_argument("--max-plies", type=int, default=300)
    parser.add_argument("--ranked", action="store_true", help="no trash talk, fixed config")
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Pin both seats to one OpenRouter endpoint, e.g. 'Baidu'. For telling a model's fault "
            "apart from a provider's: the same model served by two endpoints should fail the same "
            "way, and when it does not, the endpoint is the variable."
        ),
    )
    args = parser.parse_args()

    # Environment first, then `.env` via settings — the file is where the key actually lives for
    # everyone working on this project, and reading only `os.environ` meant `make play` refused to
    # start while every other entry point found the key.
    api_key = os.environ.get("OPENROUTER_API_KEY") or get_settings().openrouter_api_key
    if not args.scripted and not api_key:
        print(
            "OPENROUTER_API_KEY is not set in the environment or .env "
            "(use --scripted to run without one)",
            file=sys.stderr,
        )
        return 2

    white_model = "scripted/white" if args.scripted else args.white
    black_model = "scripted/black" if args.scripted else args.black

    settings = get_settings()
    redis: Redis[Any] = Redis.from_url(str(settings.redis_url))
    queue = TurnQueue(redis)
    await queue.ensure_group()
    sessionmaker = get_sessionmaker()

    if args.scripted:
        gateway = LlmGateway(completion_fn=scripted_players())
    else:
        # Pricing from the registry — the same rows the caps in ADR-0011 are computed against.
        async with sessionmaker() as session:
            pricing = await PricingTable.from_registry(session)
        gateway = LlmGateway(api_key=api_key, pricing=pricing)

    async with sessionmaker() as session:
        routing = (
            ProviderRouting(only=(args.provider,), quantizations=DEFAULT_QUANTIZATIONS)
            if args.provider
            else None
        )

        match = await create_match(
            session,
            white=Seat(display_name=white_model.split("/")[-1], model=white_model),
            black=Seat(display_name=black_model.split("/")[-1], model=black_model),
            is_ranked=args.ranked,
            max_usd=args.max_usd,
            max_plies=args.max_plies,
            routing=routing,
        )
        job = await start_match(session, queue, game_id=match.game.id)
        await session.commit()
        game_id = match.game.id

    await queue.enqueue(job)

    print(f"{BOLD}{white_model}{OFF} vs {BOLD}{black_model}{OFF}")
    print(f"{DIM}game {game_id}{OFF}")

    # The CLI is the path that actually spends money on this project, so it gets the same global
    # kill switch the API and worker tiers have (ADR-0011, layer 1). Without it `make play` was the
    # one way to blow through the daily budget — the exact hole the layer exists to close.
    budget = GlobalBudget(redis, daily_limit_usd=Decimal(str(settings.global_daily_usd_budget)))
    spent = await budget.spent_today()
    if await budget.tripped():
        print(
            f"{BOLD}Daily budget reached{OFF} — ${spent:.4f} of "
            f"${budget.limit_usd:.2f} spent today. Resets at UTC midnight.",
            file=sys.stderr,
        )
        return 1
    print(f"{DIM}budget: ${spent:.4f} of ${budget.limit_usd:.2f} used today{OFF}")

    worker = TurnWorker(
        sessionmaker=sessionmaker,
        queue=queue,
        gateway=gateway,
        redis=redis,
        consumer="cli",
        budget=budget,
    )

    board = ChessBoard()
    seen = 0
    finished = False

    while not finished:
        deliveries = await queue.consume("cli", block_ms=3000)
        if not deliveries:
            break
        for delivery in deliveries:
            await worker.process(delivery)

        async with sessionmaker() as session:
            events = list(
                await session.scalars(
                    sa.select(GameEvent)
                    .where(GameEvent.game_id == game_id, GameEvent.seq > seen)
                    .order_by(GameEvent.seq)
                )
            )
        for event in events:
            finished = render(event, board) or finished
            seen = event.seq

    await summarise(sessionmaker, game_id)

    await redis.aclose()
    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

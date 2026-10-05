#!/usr/bin/env python3
"""Can a decision model play chess at all? Play it against two fixed bots and find out.

    make anchor-trial ARGS="--dry-run"                      # the whole pipeline, no API calls
    make anchor-trial ARGS="--models cloudflare/clef --games 2"
    make anchor-trial                                       # every decision model, 10 games per anchor

**This spends money.** A game is about 130 Decisions API calls (heats included) at a few
hundred-thousandths of a dollar each. The default run (12 decision models, two anchors, ten games
each) is 240 games, roughly one to three dollars. `--dry-run` answers every question with the
scripted host instead, and costs nothing.

**Ten games, not four, is what makes the bar reachable.** With four, only 4/4 clears a 95%
interval above 50%, so a model scoring 3.5/4 would "fail"; a model that truly scores about 80%
needs around ten games to pass reliably.

Why it exists: the decision models' only games are against each other, so their ratings say how
they compare with one another and nothing about whether any of them can play. Two anchors answer
that with a question anyone can check:

* **random** — plays a uniformly random legal move.
* **greedy** — takes the most valuable piece it can capture, and plays a random move when it can
  capture nothing. It never sees a move ahead, so it walks into every trap, but it never leaves a
  piece of the model's hanging.

**The bar is fixed before the run, so the result cannot move it:** a model *passes* when it beats
`random` clearly — the lower end of the 95% interval on its score is above 50%. `greedy` is
reported and is not a gate. If the decision models do not pass, the agreed outcome is separate
leaderboards for chat and decision models, rather than one scale both are ranked on.

Every game is played through the production path for a decision seat — `build_request`, the heats
and the re-split fallback in `decision_rounds`, the action rules of `DecisionTurnRunner` (ending the
game takes a majority) and the `Referee`, which owns the board, the ply cap and every draw rule —
so a result here is a result the site would have produced. Nothing is written to the database;
every call, answered or failed, is kept verbatim in `--out`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import chess  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from chessmark.agents.decision_request import (  # noqa: E402
    ACCEPT_DRAW,
    CLAIM_DRAW,
    FIFTY_MOVES,
    OFFER_DRAW,
    RESIGN,
    THREEFOLD,
    build_request,
)
from chessmark.agents.decision_rounds import decide  # noqa: E402
from chessmark.agents.decision_turn import ENDING_ACTIONS, ENDING_MAJORITY  # noqa: E402
from chessmark.agents.decisions import Decision, DecisionGateway  # noqa: E402
from chessmark.agents.routing import ProviderRouting  # noqa: E402
from chessmark.agents.scripted_decisions import deciding  # noqa: E402
from chessmark.agents.types import LlmError  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.enums import ModelRuntime  # noqa: E402
from chessmark.db.models import ModelEndpoint, ModelRegistry  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402
from chessmark.game import Colour, Referee  # noqa: E402

#: Conventional piece values, for the greedy anchor's choice and for the material line in a report.
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


# --- The anchors ----------------------------------------------------------------------------------


def random_move(board: chess.Board, rng: random.Random) -> chess.Move:
    return rng.choice(sorted(board.legal_moves, key=lambda m: m.uci()))


def greedy_move(board: chess.Board, rng: random.Random) -> chess.Move:
    """The most valuable capture, the king never counted; otherwise random. One ply, no more."""
    best: list[chess.Move] = []
    best_value = 0
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        if board.is_en_passant(move):
            value = VALUES[chess.PAWN]
        else:
            taken = board.piece_at(move.to_square)
            value = VALUES.get(taken.piece_type, 0) if taken else 0
        if move.promotion:
            value += VALUES.get(move.promotion, 0) - VALUES[chess.PAWN]
        if value > best_value:
            best, best_value = [move], value
        elif value == best_value and value > 0:
            best.append(move)
    return rng.choice(best) if best else random_move(board, rng)


ANCHORS = {"random": random_move, "greedy": greedy_move}


# --- One game -------------------------------------------------------------------------------------


@dataclass(slots=True)
class Model:
    slug: str
    max_choices: int | None
    provider: str | None


@dataclass(slots=True)
class Game:
    model: str
    anchor: str
    model_colour: str
    result: str = "*"
    termination: str = ""
    score: float | None = None
    plies: int = 0
    moves: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)
    resplits: int = 0
    error: str | None = None


def _record(game: Game, decision: Decision) -> None:
    for failed in decision.failed_attempts:
        game.calls.append(
            {"request": failed.request, "response": failed.response, "error": failed.error}
        )
    game.calls.append({"request": decision.request, "response": decision.response, "error": None})
    game.cost_usd += float(decision.cost_usd)


async def play(
    model: Model, anchor: str, model_colour: Colour, gateway: DecisionGateway, seed: int
) -> Game:
    rng = random.Random(seed)
    referee = Referee()
    game = Game(model=model.slug, anchor=anchor, model_colour=model_colour.value)
    last_offer_ply: int | None = None

    while not referee.is_over:
        board = referee.board.raw
        if referee.side_to_move is not model_colour:
            # The anchors never resign, offer or claim; they play until a rule ends the game.
            referee.play(ANCHORS[anchor](board, rng).uci())
            continue

        # `DecisionTurnRunner._claimable`: the referee's own tests, so a claim offered is one the
        # referee would accept.
        claimable = (
            THREEFOLD
            if referee.board.is_threefold_repetition()
            else FIFTY_MOVES
            if referee.board.is_fifty_move_rule()
            else None
        )
        # `DecisionTurnRunner._may_offer`: a declined offer stands until something irreversible
        # has happened since it was made. The anchors decline every offer by playing on.
        may_offer = last_offer_ply is None or board.halfmove_clock < referee.ply - last_offer_ply
        request = build_request(
            board.copy(), draw_offered=False, draw_claimable=claimable, may_offer_draw=may_offer
        )

        async def ask(questions: dict[str, Any], request: Any = request) -> Decision:
            try:
                decision = await gateway.decide(request.body(model=model.slug, questions=questions))
            except LlmError as error:
                for failed in error.failed:
                    game.calls.append(
                        {
                            "request": failed.request,
                            "response": failed.response,
                            "error": failed.error,
                        }
                    )
                raise
            _record(game, decision)
            return decision

        try:
            answer = await decide(request, max_choices=model.max_choices, ask=ask)
        except LlmError as error:
            game.error = str(error)
            game.termination = "harness_failure"
            break
        game.resplits += answer.resplits

        # `DecisionTurnRunner._play`: the ranked-first action, unless it ends the game on less than
        # a majority, when the best non-ending action is played instead (ADR-0051).
        taken = answer.action
        if taken in ENDING_ACTIONS and answer.answers.get(taken, 0.0) <= ENDING_MAJORITY:
            taken = max(
                (a for a in request.actions if a not in ENDING_ACTIONS),
                key=lambda a: (answer.answers.get(a, 0.0), -request.actions.index(a)),
            )
        if taken == RESIGN:
            referee.resign(model_colour)
        elif taken == CLAIM_DRAW:
            referee.claim_draw()
        elif taken == ACCEPT_DRAW:  # pragma: no cover - the anchors never offer
            referee.agree_draw()
        else:
            referee.play(request.moves[answer.choice])
            if taken == OFFER_DRAW:
                last_offer_ply = referee.ply

    game.moves = [m.uci() for m in referee.board.raw.move_stack]
    game.plies = referee.ply
    if referee.is_over and referee.outcome is not None:
        outcome = referee.outcome
        game.result = outcome.result.value
        game.termination = outcome.termination.value
        game.score = (
            0.5 if outcome.winner is None else 1.0 if outcome.winner is model_colour else 0.0
        )
    return game


# --- The run --------------------------------------------------------------------------------------


async def decision_models(only: list[str] | None) -> list[Model]:
    """Every active decision model, with its option limit and the endpoint a game would pin."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                sa.select(ModelRegistry.openrouter_id, ModelRegistry.decisions_max_choices)
                .where(ModelRegistry.runtime == ModelRuntime.DECISION)
                .order_by(ModelRegistry.openrouter_id)
            )
        ).all()
        endpoint_rows = (
            await session.execute(
                sa.select(ModelRegistry.openrouter_id, ModelEndpoint.provider_name)
                .join(ModelEndpoint, ModelEndpoint.model_id == ModelRegistry.id)
                .where(
                    ModelRegistry.runtime == ModelRuntime.DECISION,
                    ModelEndpoint.is_active.is_(True),
                )
                .order_by(ModelEndpoint.provider_name)
            )
        ).all()
    endpoints: dict[str, str] = {str(slug): str(provider) for slug, provider in endpoint_rows}
    models = [Model(slug, cap, endpoints.get(slug)) for slug, cap in rows]
    if only:
        models = [m for m in models if m.slug in only]
    return models


def interval(score: float, n: int) -> tuple[float, float]:
    """Wilson's 95% interval on a score, a draw counting half. Honest at small n and at 0 or 1."""
    if n == 0:
        return (0.0, 1.0)
    z = 1.96
    centre = (score + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(score * (1 - score) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", nargs="*", help="decision model slugs; default every one")
    parser.add_argument("--anchors", nargs="*", default=list(ANCHORS), choices=list(ANCHORS))
    parser.add_argument(
        "--games", type=int, default=10, help="per model per anchor, colours alternate"
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path("anchor-trial"))
    parser.add_argument("--dry-run", action="store_true", help="scripted host, no API calls")
    args = parser.parse_args()

    try:
        models = await decision_models(args.models)
    finally:
        await dispose_engine()
    if not models:
        print("no decision models matched", file=sys.stderr)
        return 1

    settings = get_settings()
    args.out.mkdir(parents=True, exist_ok=True)
    limit = asyncio.Semaphore(args.concurrency)
    games: list[Game] = []

    async def one(model: Model, anchor: str, index: int) -> None:
        colour = Colour.WHITE if index % 2 == 0 else Colour.BLACK
        gateway = DecisionGateway(
            api_key="" if args.dry_run else settings.openrouter_api_key,
            decide_fn=deciding(max_choices=model.max_choices) if args.dry_run else None,
            routing=ProviderRouting(only=(model.provider,), allow_fallbacks=False)
            if model.provider
            else None,
        )
        async with limit:
            started = time.perf_counter()
            game = await play(model, anchor, colour, gateway, seed=args.seed * 1000 + index)
        games.append(game)
        name = f"{model.slug.replace('/', '__')}__{anchor}__{index}.json"
        record = asdict(game) | {"seconds": round(time.perf_counter() - started, 1)}
        (args.out / name).write_text(json.dumps(record, indent=1, default=str))
        print(
            f"{model.slug:42s} vs {anchor:6s} as {colour.value:5s}: "
            f"{game.result:7s} {game.termination:22s} {game.plies:3d} plies  ${game.cost_usd:.4f}"
            + (f"  [{game.error[:80]}]" if game.error else ""),
            flush=True,
        )

    await asyncio.gather(
        *(one(m, a, i) for m in models for a in args.anchors for i in range(args.games))
    )

    print("\n| model | anchor | W/D/L/failed | score | 95% interval | passes |")
    print("|---|---|---|---|---|---|")
    verdicts: dict[str, bool] = {}
    for model in models:
        for anchor in args.anchors:
            mine = [g for g in games if g.model == model.slug and g.anchor == anchor]
            scored = [g.score for g in mine if g.score is not None]
            w, d, lo = scored.count(1.0), scored.count(0.5), scored.count(0.0)
            failed = len(mine) - len(scored)
            score = sum(scored) / len(scored) if scored else 0.0
            low, high = interval(score, len(scored))
            passes = low > 0.5
            if anchor == "random":
                verdicts[model.slug] = passes
            gate = ("yes" if passes else "no") if anchor == "random" else "(not a gate)"
            print(
                f"| {model.slug} | {anchor} | {w}/{d}/{lo}/{failed} | {score:.2f} "
                f"| {low:.2f} to {high:.2f} | {gate} |"
            )
    spent = sum(g.cost_usd for g in games)
    # A dry run's host prices its calls too, so the figure is labelled rather than presented as money.
    cost = f"Scripted cost ${spent:.4f} (nothing spent)" if args.dry_run else f"Spent ${spent:.4f}"
    passed = sum(verdicts.values())
    print(f"\n{passed} of {len(verdicts)} models beat random clearly. {cost}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

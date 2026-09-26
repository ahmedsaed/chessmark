#!/usr/bin/env python3
"""Probe decision models on labelled positions, and print what they choose (ADR-0049, ADR-0051).

    make probe-decisions                       # every decision model in the registry
    make probe-decisions ARGS="--model typesafe/jev-1.13"

**Spends money** — a fraction of a cent: one Decisions API call per position per model, each
about 1,500 input tokens at $0.02-0.05 per million. Run by hand, never by a suite.

**A diagnostic, not a step.** Under `d1` this set the gates a `noul` needed, and it had to be run
for every new model because a threshold does not carry between models. `d2` asks what to do with
the turn as one `choice`, and the option a model ranks first is what happens — there is no
threshold left to set, and a new model plays as soon as it passes its capability check
(`agents/decision_check.py`). This is for a person who wants to see how a model judges.

Each position names the actions a sound player could take there, and the output marks whether the
model's first-ranked action is one of them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

import chess  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from chessmark.agents.decision_request import (  # noqa: E402
    ACCEPT_DRAW,
    ACTION_QUESTION,
    CLAIM_DRAW,
    MOVE_QUESTION,
    OFFER_DRAW,
    PLAY_ON,
    RESIGN,
    THREEFOLD,
    build_request,
)
from chessmark.agents.decisions import DecisionGateway  # noqa: E402
from chessmark.agents.types import LlmError  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.enums import ModelRuntime  # noqa: E402
from chessmark.db.models import ModelRegistry  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


@dataclass(frozen=True)
class Probe:
    name: str
    fen: str
    #: The actions a sound player could take here. More than one where more than one is sound.
    sound: frozenset[str]
    #: The move a sound player finds, when there is one clear answer.
    best: str | None = None
    offered: bool = False
    claimable: bool = False


LEVEL = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
MATE_IN_ONE = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
QUEEN_UP = "4k3/8/8/8/8/8/3Q4/4K3 w - - 0 1"
LOST = "4k3/8/8/8/8/8/3qr3/4K3 w - - 0 1"
DEAD_DRAW = "8/5k2/8/8/8/8/1r3K2/7R w - - 0 60"
PAWN_DOWN = "r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 8"

PROBES = [
    Probe("level opening", LEVEL, frozenset({PLAY_ON})),
    Probe("level opening, offered", LEVEL, frozenset({PLAY_ON}), offered=True),
    Probe("mate in one", MATE_IN_ONE, frozenset({PLAY_ON}), best="Qxf7"),
    Probe("a queen up", QUEEN_UP, frozenset({PLAY_ON})),
    Probe("a queen up, offered", QUEEN_UP, frozenset({PLAY_ON}), offered=True),
    Probe("a queen up, threefold claimable", QUEEN_UP, frozenset({PLAY_ON}), claimable=True),
    Probe("a queen and rook down", LOST, frozenset({RESIGN, OFFER_DRAW})),
    Probe("a queen and rook down, offered", LOST, frozenset({ACCEPT_DRAW}), offered=True),
    Probe("a queen and rook down, claimable", LOST, frozenset({CLAIM_DRAW}), claimable=True),
    Probe("dead drawn rook ending", DEAD_DRAW, frozenset({OFFER_DRAW, PLAY_ON})),
    Probe("dead drawn rook ending, offered", DEAD_DRAW, frozenset({ACCEPT_DRAW}), offered=True),
    Probe("a pawn down, open middlegame", PAWN_DOWN, frozenset({PLAY_ON})),
]


async def decision_models(requested: list[str]) -> list[str]:
    if requested:
        return requested
    async with session_scope() as session:
        rows = await session.scalars(
            sa.select(ModelRegistry.openrouter_id).where(
                ModelRegistry.runtime == ModelRuntime.DECISION, ModelRegistry.enabled.is_(True)
            )
        )
        return sorted(rows)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append", default=[], help="a decision model's slug")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2

    try:
        models = await decision_models(args.model)
    finally:
        await dispose_engine()
    if not models:
        print("no decision models registered — run `make seed-models` first", file=sys.stderr)
        return 1

    gateway = DecisionGateway(api_key=settings.openrouter_api_key)
    spent = 0.0
    for model in models:
        print(f"\n{model}")
        sound = asked = 0
        for probe in PROBES:
            request = build_request(
                chess.Board(probe.fen),
                draw_offered=probe.offered,
                draw_claimable=THREEFOLD if probe.claimable else None,
            )
            try:
                decision = await gateway.decide(request.body(model=model))
            except LlmError as error:
                # One refused position is a gap in the table, not a reason to lose the rest of it:
                # Kev's host rate-limits on tokens per minute, and a probe run straight after a game
                # meets that limit part-way through.
                print(f"  {probe.name:<42} refused: {str(error)[:80]}")
                continue
            spent += float(decision.cost_usd)
            asked += 1
            chosen, _ = decision.choice(MOVE_QUESTION, set(request.moves))
            action, ranked = decision.choice(ACTION_QUESTION, set(request.actions))
            found = "" if probe.best is None else (" ✓" if chosen == probe.best else " ✗")
            ok = action in probe.sound
            sound += ok
            spread = "  ".join(
                f"{a} {p:.2f}" for a, p in sorted(ranked.items(), key=lambda kv: -kv[1])
            )
            print(
                f"  {probe.name:<42} {chosen:<6}{found:<3} {'✓' if ok else '✗'} {action:<12} {spread}"
            )
        print(f"  sound actions: {sound} of {asked}")

    print(f"\nspent ${spent:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

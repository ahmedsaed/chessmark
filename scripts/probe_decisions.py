#!/usr/bin/env python3
"""Probe decision models on labelled positions, and print what they answer (ADR-0049).

    make probe-decisions                       # every decision model in the registry
    make probe-decisions ARGS="--model typesafe/jev-1.13"

**Spends money** — a fraction of a cent: one Decisions API call per position per model, each
about 1,500 input tokens at $0.042 per million. Run by hand, never by a suite.

The gates in `agents/decision_turn.py` are set from what this prints, not from a default. A
decision model's `noul` near 0.5 means yes and no are similarly likely, not "somewhat yes", so
0.5 is a guess until a probe says where a model's clear yes and clear no actually fall — and the
first real game showed the guess drawing a level game at move seven. Run it again whenever
`DECISION_VERSION` changes or a new decision model is listed: a threshold does not carry from one
model to another, or from one wording to the next.

Each position states what a sound player would answer. The output puts the model's number beside
that expectation, so a gate can be placed between the yeses and the noes it actually produced.
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
    ACCEPT_DRAW_QUESTION,
    CLAIM_DRAW_QUESTION,
    MOVE_QUESTION,
    OFFER_DRAW_QUESTION,
    RESIGN_QUESTION,
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
    #: What a sound player answers to each yes-or-no question asked here. Absent means "not asked".
    expect: dict[str, bool]
    #: The move a sound player finds, when there is one clear answer.
    best: str | None = None
    offered: bool = False
    claimable: bool = False


PROBES = [
    Probe(
        "level opening",
        "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
        {OFFER_DRAW_QUESTION: False, RESIGN_QUESTION: False},
    ),
    Probe(
        "level opening, offered",
        "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
        {ACCEPT_DRAW_QUESTION: False, RESIGN_QUESTION: False},
        offered=True,
    ),
    Probe(
        "mate in one",
        "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
        {OFFER_DRAW_QUESTION: False, RESIGN_QUESTION: False},
        best="Qxf7",
    ),
    Probe(
        "a queen up",
        "4k3/8/8/8/8/8/3Q4/4K3 w - - 0 1",
        {OFFER_DRAW_QUESTION: False, RESIGN_QUESTION: False},
    ),
    Probe(
        "a queen up, offered",
        "4k3/8/8/8/8/8/3Q4/4K3 w - - 0 1",
        {ACCEPT_DRAW_QUESTION: False, RESIGN_QUESTION: False},
        offered=True,
    ),
    Probe(
        "a queen and rook down",
        "4k3/8/8/8/8/8/3qr3/4K3 w - - 0 1",
        {OFFER_DRAW_QUESTION: True, RESIGN_QUESTION: True},
    ),
    Probe(
        "a queen and rook down, offered",
        "4k3/8/8/8/8/8/3qr3/4K3 w - - 0 1",
        {ACCEPT_DRAW_QUESTION: True, RESIGN_QUESTION: True},
        offered=True,
    ),
    Probe(
        "dead drawn rook ending",
        "8/5k2/8/8/8/8/1r3K2/7R w - - 0 60",
        {OFFER_DRAW_QUESTION: True, RESIGN_QUESTION: False},
    ),
    Probe(
        "dead drawn rook ending, offered",
        "8/5k2/8/8/8/8/1r3K2/7R w - - 0 60",
        {ACCEPT_DRAW_QUESTION: True, RESIGN_QUESTION: False},
        offered=True,
    ),
    Probe(
        "a queen up, threefold claimable",
        "4k3/8/8/8/8/8/3Q4/4K3 w - - 0 1",
        {CLAIM_DRAW_QUESTION: False, RESIGN_QUESTION: False},
        claimable=True,
    ),
    Probe(
        "a queen and rook down, threefold claimable",
        "4k3/8/8/8/8/8/3qr3/4K3 w - - 0 1",
        {CLAIM_DRAW_QUESTION: True},
        claimable=True,
    ),
    Probe(
        "a pawn down, open middlegame",
        "r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 8",
        {RESIGN_QUESTION: False},
    ),
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
        yes: dict[str, list[float]] = {}
        no: dict[str, list[float]] = {}
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
                print(f"  {probe.name:<44} refused: {str(error)[:80]}")
                continue
            spent += float(decision.cost_usd)
            chosen, _ = decision.choice(MOVE_QUESTION, set(request.moves))
            found = "" if probe.best is None else (" ✓" if chosen == probe.best else " ✗")
            answers = []
            for question, expected in probe.expect.items():
                value = decision.noul(question)
                (yes if expected else no).setdefault(question, []).append(value)
                answers.append(f"{question}={value:.2f}{'(yes)' if expected else '(no)'}")
            print(f"  {probe.name:<44} {chosen:<6}{found:<3} " + "  ".join(answers))

        print("  where a gate can go — highest 'no' against lowest 'yes', per question:")
        for question in sorted(set(yes) | set(no)):
            high_no = max(no.get(question, [0.0]))
            low_yes = min(yes.get(question, [1.0]))
            verdict = "separable" if high_no < low_yes else "OVERLAP"
            print(f"    {question:<12} no ≤ {high_no:.2f}   yes ≥ {low_yes:.2f}   {verdict}")

    print(f"\nspent ${spent:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

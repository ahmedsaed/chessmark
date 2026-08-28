#!/usr/bin/env python3
"""Reopen a game the harness stopped.

    make resume GAME=<id> USD=1.50

Only endings **Chessmark** imposed can be reopened — a budget, its own ply cap, a provider it
could not reach. A checkmate is final, and so is a forfeit: both are findings about a player, and
un-ending one would let a bad result be replayed until it improved. The refusal is the point of
this script existing rather than a hand-edited `UPDATE`.

Appends a `game_resumed` event, so the reason a finished game started moving again is in the same
log everything else reads (ADR-0008, invariant 7).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from redis.asyncio import Redis  # noqa: E402

from chessmark.agents.prompts import PROMPT_VERSION  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.enums import EventType, GameStatus  # noqa: E402
from chessmark.db.models import TournamentGame  # noqa: E402
from chessmark.db.repositories import append_event, get_game, rebuild_referee  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import (  # noqa: E402
    RESUMABLE_TERMINATIONS,
    GameResult,
    Termination,
)
from chessmark.orchestration import AdvanceTurn, TurnQueue  # noqa: E402

#: The two draws that were applied without a claim before ADR-0020.
_UNCLAIMED = frozenset({Termination.THREEFOLD_REPETITION, Termination.FIFTY_MOVE_RULE})


def _unclaimed_draw_is_reopenable(game: Any) -> tuple[bool, str]:
    """Whether this specific draw was one the players never had a say in.

    Deliberately narrow, because the general rule must keep holding: a chess result is final, and a
    script that can reopen any draw is a script that can replay a bad result until it improves.

    Two conditions, both necessary. The termination has to be one of the claimable pair — a
    checkmate or a fivefold backstop is nobody's fault but the player's. And the game has to
    predate the prompt that disclosed the rule: from v2 on, a model is told that repetition is
    claimable and given `claim_draw`, so a draw it walked into is a finding about it.
    """
    if game.termination not in _UNCLAIMED:
        return False, f"{game.termination} was never an unclaimed draw"

    if game.prompt_version == PROMPT_VERSION:
        return False, (
            f"this game ran under prompt {game.prompt_version}, which states the rule and offers "
            "`claim_draw` — walking into the draw was its own doing"
        )

    return True, (
        f"played under prompt {game.prompt_version}, which never mentioned the rule; "
        f"ratings are for {PROMPT_VERSION}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_id")
    parser.add_argument(
        "--max-usd",
        type=Decimal,
        default=None,
        help="New per-game budget. Must exceed what has already been spent, or the game stops again immediately.",
    )
    parser.add_argument("--max-plies", type=int, default=None, help="New ply cap.")
    parser.add_argument(
        "--unclaimed-draw",
        action="store_true",
        help=(
            "reopen a game drawn by threefold repetition or the fifty-move rule that nobody "
            "claimed. Only valid for a game played under a prompt that never disclosed the rule."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    sessionmaker = get_sessionmaker()
    redis: Redis[Any] = Redis.from_url(str(settings.redis_url))
    queue = TurnQueue(redis)
    await queue.ensure_group()

    try:
        async with sessionmaker() as session:
            game = await get_game(session, args.game_id)

            if game.status is GameStatus.RUNNING:
                print("game is already running", file=sys.stderr)
                return 1

            resumable = game.termination in RESUMABLE_TERMINATIONS
            if not resumable and args.unclaimed_draw:
                resumable, refusal = _unclaimed_draw_is_reopenable(game)
                if not resumable:
                    print(refusal, file=sys.stderr)
                    return 2
                print(f"reopening an unclaimed {game.termination} draw ({refusal})")

            if not resumable:
                print(
                    f"refusing to reopen a game that ended by {game.termination}. "
                    "Only a budget, a ply cap, or an unreachable provider may be reopened — "
                    "a chess result and a forfeit are findings about a player."
                    + (
                        " An automatic threefold or fifty-move draw from before ADR-0020 can be "
                        "reopened with --unclaimed-draw."
                        if game.termination in _UNCLAIMED
                        else ""
                    ),
                    file=sys.stderr,
                )
                return 2

            if args.max_usd is not None:
                if args.max_usd <= game.total_cost_usd:
                    print(
                        f"budget ${args.max_usd} is at or below the ${game.total_cost_usd:.4f} "
                        "already spent — the game would stop again on its first turn",
                        file=sys.stderr,
                    )
                    return 2
                game.max_usd = args.max_usd

            if args.max_plies is not None:
                game.max_plies = args.max_plies

            referee = await rebuild_referee(session, game)

            # Clearing the outcome is what actually reopens it: `rebuild_referee` re-applies a
            # stored termination on every turn, so a game left FINISHED would conclude again
            # before playing a move.
            previous = game.termination
            game.status = GameStatus.RUNNING

            # **The pairing has to be un-settled too, and both halves of it.** A pairing carries
            # either an `abandoned_reason` or a `white_score`, and a resumed game must shed
            # whichever it has: the verdict being reopened is exactly the one written there.
            #
            # Clearing only the abandonment was the first version of this, and it left a worse bug
            # than it fixed. `white_score` means "this pairing is decided" — the column's own
            # comment says *null while the game is unplayed or in flight* — so four resumed games
            # ran for up to 89 plies while the schedule showed them as **played**, with the score
            # of the forfeit that had just been overturned, and the event reported `live: 0` with
            # four games moving. The homepage, reading the games directly, disagreed with the
            # tournament page, which is how it was noticed.
            pairing = await session.scalar(
                sa.select(TournamentGame).where(TournamentGame.game_id == game.id)
            )
            if pairing is not None and (
                pairing.abandoned_reason is not None or pairing.white_score is not None
            ):
                was = "abandoned" if pairing.abandoned_reason else f"scored {pairing.white_score}"
                pairing.abandoned_reason = None
                pairing.white_score = None
                pairing.ended_at = None
                print(f"re-opened its tournament pairing (was {was}) so it can settle again")
            game.result = GameResult.ONGOING
            game.termination = None
            game.termination_detail = None
            game.ended_at = None

            await append_event(
                session,
                game_id=game.id,
                type=EventType.GAME_RESUMED,
                payload={
                    "previous_termination": str(previous),
                    "ply": referee.ply,
                    "max_usd": str(game.max_usd) if game.max_usd else None,
                    "max_plies": game.max_plies,
                    "spent_usd": str(game.total_cost_usd),
                },
            )
            await session.commit()
            job = AdvanceTurn(game_id=game.id, expected_ply=referee.ply)

        await queue.enqueue(job)
        print(
            f"resumed {game.id} from ply {referee.ply} "
            f"(was {previous}, spent ${game.total_cost_usd:.4f}, budget now ${game.max_usd})"
        )
        print("a worker must be running: make worker")
        return 0
    finally:
        await redis.aclose()
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

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
from chessmark.db.models import GameEvent, Player, TournamentGame  # noqa: E402
from chessmark.db.repositories import append_event, get_game, rebuild_referee  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import (  # noqa: E402
    FORFEIT_TERMINATIONS,
    RESUMABLE_TERMINATIONS,
    GameResult,
    Termination,
)
from chessmark.orchestration import AdvanceTurn, TurnQueue  # noqa: E402

#: The two draws that were applied without a claim before ADR-0020.
_UNCLAIMED = frozenset({Termination.THREEFOLD_REPETITION, Termination.FIFTY_MOVE_RULE})


async def _clear_stale_forfeits(session: Any, game: Any, previous: Any) -> int:
    """Drop the seat's `forfeited` flag when the ending that wrote it is being reopened.

    **The same shape as un-settling the pairing, and it was missed for the same reason.** The flag
    is a *verdict*, it was written by the ending this reopens, and unlike the pairing's score it is
    published: `bench.service` counts it into the leaderboard's forfeits column, over exactly the
    games a resume makes ratable again.

    It gets set for a harness stop because `BUDGET_EXCEEDED` travels as `TurnStatus.FORFEITED` —
    the turn does end the game — while `ratable.HARNESS_TERMINATIONS` says just as plainly that it
    is not a finding. Two free-pool games were budget-stopped, reopened, and played on to a real
    checkmate and a real threefold draw; both stayed ratable and both models carried a forfeit
    nothing in their play had earned (ADR-0024). The turn loop no longer writes one; this clears
    the ones it already wrote.

    Refuses when the ending being reopened *was* a forfeit. No resumable termination is one today,
    so this never fires — it is what keeps the function honest if that ever changes, because
    clearing the flag on a genuine forfeit would erase the finding rather than a mistake.
    """
    if previous in FORFEIT_TERMINATIONS:
        return 0
    cleared = await session.execute(
        sa.update(Player)
        .where(Player.game_id == game.id, Player.forfeited.is_(True))
        .values(forfeited=False)
    )
    return int(cleared.rowcount or 0)


async def _verdict_was_overwritten(session: Any, game: Any) -> tuple[bool, str]:
    """Whether this game's stored ending replaced an earlier one written by a race.

    A game should append one `game_ended` row. Before ADR-0022, two workers could play the same ply
    at once and the loser wrote its verdict over the winner's — one game ended **seven** times.
    Where the first ending was a harness stop and a later one is a forfeit, the rated verdict was
    chosen by scheduling.

    Reopens on the **first** ending, which is the one the race overwrote. Not the most favourable —
    the first, whatever it says — because a script that picks among real endings is a script that
    can improve a result by running it again.
    """
    endings = list(
        await session.scalars(
            sa.select(GameEvent)
            .where(GameEvent.game_id == game.id, GameEvent.type == EventType.GAME_ENDED)
            .order_by(GameEvent.seq)
        )
    )
    if len(endings) < 2:
        return False, "this game ended once; there is no overwritten verdict to restore"

    first = str(endings[0].payload.get("termination") or "")
    if first == str(game.termination):
        return False, f"the stored verdict is already the first one written ({first})"
    if first not in {str(t) for t in RESUMABLE_TERMINATIONS}:
        return False, f"the first ending was {first}, which is a finding about a player"
    return True, f"{len(endings)} endings recorded; the first was {first}, overwritten by a race"


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
        "--overwritten-verdict",
        action="store_true",
        help=(
            "reopen a game whose stored ending replaced an earlier harness stop, written when two "
            "workers played the same ply at once (ADR-0022)."
        ),
    )
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

            # This reopens a *forfeit*, which the general rule refuses, so it is gated on the
            # record rather than on the flag: the operator says which correction they mean and the
            # event log decides whether it applies.
            #
            # `--harness-ceiling` stood here too, reopening a `truncated` forfeit where the stored
            # calls showed our own `max_tokens` had cut the response. It is gone because the
            # question it asked no longer has two answers: a truncation is a harness stop either
            # way (ADR-0024), so `TRUNCATED` is in `RESUMABLE_TERMINATIONS` and a plain resume
            # reopens it. A flag that can never fire is worse than no flag.
            if not resumable and args.overwritten_verdict:
                resumable, why = await _verdict_was_overwritten(session, game)
                if not resumable:
                    print(why, file=sys.stderr)
                    return 2
                print(f"reopening a verdict a race overwrote ({why})")

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
                    )
                    + " If a race overwrote an earlier harness stop, --overwritten-verdict does.",
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

            cleared = await _clear_stale_forfeits(session, game, previous)
            if cleared:
                print(
                    f"cleared a stale forfeit on {cleared} seat(s) "
                    f"(written by the {previous} this reopens)"
                )

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

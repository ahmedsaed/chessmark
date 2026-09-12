#!/usr/bin/env python3
"""Re-end a game the harness scored against the wrong party (invariant 11, ADR-0015).

    make repair-verdict ARGS="<game-id>"                  # report what it would do
    make repair-verdict ARGS="<game-id> --write"
    make repair-verdict ARGS="<game-id> --replay --write" # ...and let the pool pair it again

A forfeit is a finding about a model: it ran out of illegal-move retries, or it would not call a
tool. That claim has to be true. Three games were published with it and none of them was the
model's doing — two where `dots-3-note-preview`'s endpoint delivered tool-call markup as prose, and
one where `nemotron-3.5-lightning` returned multilingual noise for four replies straight, on a
77,264-token prompt inside a 1,000,000-token window, with no compaction and `finish_reason: stop`.
A model does not lose the ability to form words at move 66.

**The record is corrected by appending, not by editing.** A second `game_ended` event is written
saying what the ending should have been and why, and the game row is brought into line with it. The
log therefore holds both endings in order, which is what ADR-0008 asks of it and what makes the
correction checkable rather than merely asserted: nothing is overwritten, and `llm_calls`,
`plies` and the transcript are untouched.

`abandoned` is the destination because it is already the classification for "we could not get a
result through", and `HARNESS_TERMINATIONS` excludes it from every rating (invariant 11). The game
stays on the site, with its reason on the page.

**`--replay` is separate on purpose.** Clearing a pairing's scores lets a pool schedule it again,
which is right when the fixture never really happened — but it is a change to an event's schedule
rather than to a game's record, and the two decisions are not the same decision.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.db.enums import EventType, GameStatus  # noqa: E402
from chessmark.db.models import Game, TournamentGame  # noqa: E402
from chessmark.db.repositories import append_event  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import GameResult, Termination  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, AMBER = "\033[31m", "\033[32m", "\033[33m"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", help="the game id to re-end")
    parser.add_argument(
        "--reason",
        required=True,
        help="why the published ending was wrong. Recorded on the event, and read by people.",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="also clear the tournament pairing, so the pool may schedule the fixture again",
    )
    parser.add_argument("--write", action="store_true", help="apply it. Without this, a report.")
    args = parser.parse_args()

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session, session.begin():
            game = await session.get(Game, uuid.UUID(args.game))
            if game is None:
                print(f"{RED}no game {args.game}{OFF}", file=sys.stderr)
                return 1

            if game.status not in (GameStatus.FINISHED, GameStatus.ABORTED):
                print(f"{RED}game is {game.status.value}; only a finished one can be re-ended{OFF}")
                return 1

            if game.termination is Termination.ABANDONED:
                print(f"{AMBER}already abandoned; nothing to correct{OFF}")
                return 0

            pairing = await session.scalar(
                sa.select(TournamentGame).where(TournamentGame.game_id == game.id)
            )

            print(f"{BOLD}{game.id}{OFF}")
            print(f"  was    {game.termination.value if game.termination else '—'}  {game.result}")
            print(f"  now    abandoned  *   {DIM}(excluded from every rating){OFF}")
            print(f"  reason {args.reason}")
            if pairing is not None:
                scored = pairing.white_score is not None
                print(
                    f"  pool   round {pairing.round_number}, "
                    f"{'scored' if scored else 'unscored'}"
                    + (f" — {'cleared for a replay' if args.replay else 'left as it is'}")
                )

            if not args.write:
                print(f"\n{DIM}dry run; pass --write to apply{OFF}")
                return 0

            # Appended first, so the log reads in the order it happened: the original ending, then
            # the correction. A reader can see both and check the second against the first.
            await append_event(
                session,
                game_id=game.id,
                type=EventType.GAME_ENDED,
                payload={
                    "result": str(GameResult.ONGOING),
                    "winner": None,
                    "termination": str(Termination.ABANDONED),
                    "ply_count": game.ply_count,
                    "detail": f"Re-ended: {args.reason}",
                    "corrects": game.termination.value if game.termination else None,
                },
            )

            game.status = GameStatus.ABORTED
            game.termination = Termination.ABANDONED
            game.result = GameResult.ONGOING
            game.winner_colour = None

            print(f"{GREEN}re-ended as abandoned{OFF}")

            if args.replay and pairing is not None:
                pairing.white_score = None
                pairing.black_score = None
                pairing.game_id = None
                pairing.abandoned_reason = None
                print(f"{GREEN}pairing cleared; the pool may schedule it again{OFF}")
            elif args.replay:
                print(f"{AMBER}no tournament pairing for this game{OFF}")

        print(f"\n{DIM}the leaderboard rebuilds itself on the next read (ADR-0032){OFF}")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

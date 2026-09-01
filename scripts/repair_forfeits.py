#!/usr/bin/env python3
"""Make every seat's `forfeited` flag agree with its game's own record (ADR-0024).

    make repair-forfeits            # report what it would do
    make repair-forfeits ARGS=--write

**The game record is the authority** (invariant 1): where a stored verdict and the game disagree,
the game wins. `Player.forfeited` is a stored verdict, and it is a *published* one — `bench.service`
counts it into the leaderboard's forfeits column — so a stale one is a claim about a model on a page
people read.

Two ways it drifted, both of them writing a forfeit nobody earned:

* **A harness stop set it.** `turn.py` wrote the flag from `result.status is TurnStatus.FORFEITED`,
  and `BUDGET_EXCEEDED` travels that way because it does end the game — while
  `ratable.HARNESS_TERMINATIONS` says just as plainly that it is not a finding.
* **A resume did not clear it.** Two free-pool games were budget-stopped, reopened, and played on to
  a genuine checkmate and a genuine threefold draw. Both endings are correct and both games are
  rated; both models carried a forfeit from the ending that had been overturned.

`turn.py` no longer writes one and `resume_game.py` now clears one, so this is about the rows those
already wrote. It is **not** a substitute for either: nothing here reopens a game or changes a
result, and a game whose result is genuinely a forfeit keeps its flag.

Why a sweep rather than a one-shot for the two known games: the rule is derivable. A seat is
forfeited exactly when its game ended by a forfeit *against that seat*, which the game record
already says. Anything else is drift, and a rule that can be checked is a rule that stays true.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.db.enums import GameStatus  # noqa: E402
from chessmark.db.models import Game, Player  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402
from chessmark.game import FORFEIT_TERMINATIONS  # noqa: E402


def should_be_forfeited(
    *, status: GameStatus, termination: object, winner_colour: object, colour: object
) -> bool:
    """Whether this seat's flag should be set, according to the game.

    Three conditions, and each one rules out a case that reached production:

    * the game is **finished** — an aborted or running game has no verdict to carry, and an
      abandoned one is explicitly not a finding about anybody;
    * its termination is a **forfeit**, not a harness stop that merely travelled like one;
    * and it went **against this seat**. `referee.forfeit(colour, …)` makes the forfeiting side
      lose, so the flag belongs to whoever is not the winner. A forfeit with no winner recorded is
      unattributable and left alone rather than guessed at.
    """
    if status is not GameStatus.FINISHED:
        return False
    if termination not in FORFEIT_TERMINATIONS:
        return False
    if winner_colour is None:
        return False
    return colour != winner_colour


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="apply the change. Without it, nothing is written and the report is the output.",
    )
    parser.add_argument("--game", default=None, help="restrict to one game id")
    args = parser.parse_args()

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session, session.begin():
            query = (
                sa.select(
                    Player.id,
                    Player.colour,
                    Player.display_name,
                    Player.forfeited,
                    Game.id.label("game_id"),
                    Game.status,
                    Game.termination,
                    Game.winner_colour,
                )
                .join(Game, Game.id == Player.game_id)
                .order_by(Game.created_at, Player.colour)
            )
            if args.game:
                query = query.where(Game.id == args.game)

            clear: list[tuple[object, str]] = []
            set_: list[tuple[object, str]] = []
            for row in (await session.execute(query)).all():
                expected = should_be_forfeited(
                    status=row.status,
                    termination=row.termination,
                    winner_colour=row.winner_colour,
                    colour=row.colour,
                )
                if row.forfeited == expected:
                    continue
                why = (
                    f"{row.game_id} {row.colour.value} ({row.display_name}) — game is "
                    f"{row.status.value} by {row.termination}, winner "
                    f"{row.winner_colour.value if row.winner_colour else 'none'}"
                )
                (set_ if expected else clear).append((row.id, why))

            if not clear and not set_:
                print("every forfeit flag already agrees with its game")
                return 0

            # Reported apart, because they are not the same risk. Clearing removes a claim we
            # cannot support; setting *adds* one to a published column, and an operator should see
            # that separately rather than inside a total.
            if clear:
                print(f"to clear — flagged, but the game says otherwise ({len(clear)}):")
                for _, why in clear:
                    print(f"  {why}")
            if set_:
                print(
                    f"\nto set — the game records a forfeit and the flag is missing ({len(set_)}):"
                )
                for _, why in set_:
                    print(f"  {why}")

            if not args.write:
                print("\ndry run; pass --write to apply")
                return 0

            for ids, value in ((clear, False), (set_, True)):
                if ids:
                    await session.execute(
                        sa.update(Player)
                        .where(Player.id.in_([player_id for player_id, _ in ids]))
                        .values(forfeited=value)
                    )
            print(f"\ncleared {len(clear)}, set {len(set_)}")
            print("the leaderboard recomputes on its next read; nothing else to run")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

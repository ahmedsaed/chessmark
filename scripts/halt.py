#!/usr/bin/env python3
"""Stop every model call in the system, or start them again (OPS-19).

    ./chessmark halt                     # is it on, and why?
    ./chessmark halt "paying for a bug"  # stop everything
    ./chessmark halt --clear             # start again

Different from the daily spend limit beside it, and both are worth having. That one is a **number**
in config, compared against a counter that resets at UTC midnight. This is a **state**: something is
wrong, or somebody said stop, and it stays until that changes. Nothing else can be flipped at
runtime — the kill switch is read from the environment, so using it means editing `.env` and
restarting.

A halt set by a **402** lifts itself: the reconciler probes the account balance every five minutes
and starts again once there is credit. A halt set **here** never does. Somebody meant it, and a
probe deciding otherwise would be the system overruling its operator, so this script is the only
way back.

**Nothing is forfeited and no game is ended.** A halted turn is not run and its job is dropped; the
game stays `RUNNING` and comes back on its own. A model must never lose a game because we stopped
the harness (invariant 11).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

from redis.asyncio import Redis  # noqa: E402

from chessmark.core.config import get_settings  # noqa: E402
from chessmark.core.halt import SOURCE_OPERATOR, Halt  # noqa: E402


def _ago(at: dt.datetime) -> str:
    seconds = int((dt.datetime.now(dt.UTC) - at).total_seconds())
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    return f"{seconds / 3600:.1f}h ago"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reason", nargs="?", help="why you are stopping it")
    parser.add_argument("--clear", action="store_true", help="lift the halt and resume")
    args = parser.parse_args()

    if args.clear and args.reason:
        parser.error("pass a reason to halt, or --clear to resume — not both")

    redis: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    halt = Halt(redis)
    try:
        if args.clear:
            if await halt.clear():
                print("halt lifted — model calls resume")
                print("a worker must be running for games to move: ./chessmark status")
                return 0
            print("nothing was halted")
            return 0

        if args.reason:
            state = await halt.set(args.reason, source=SOURCE_OPERATOR)
            if state.reason != args.reason:
                # Something was already halted. Reported rather than overwritten: the first halt
                # wins, so an operator does not silently replace a credit halt (or another
                # operator's) with their own and lose the original reason.
                print(f"already halted ({state.source}): {state.reason}")
                print("lift it first if you meant to replace it: ./chessmark halt --clear")
                return 1
            print(f"halted — {state.reason}")
            print("games are left running and nothing is forfeited; lift it with --clear")
            return 0

        state = await halt.state()
        if state is None:
            print("running — nothing is halted")
            return 0

        print(f"halted ({state.source}) {_ago(state.at)}: {state.reason}")
        if state.balance_usd is not None:
            print(f"the account held ${state.balance_usd} when it was set")
        if state.self_clearing:
            print("this lifts itself once the account has credit again")
        else:
            print("this was set by hand and will not lift itself: ./chessmark halt --clear")
        return 0
    finally:
        await redis.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

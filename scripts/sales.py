#!/usr/bin/env python3
"""Open credit sales, pause them, or see which they are (ADR-0057).

    ./chessmark sales                      # open or paused, and why
    ./chessmark sales open                 # take payments
    ./chessmark sales pause "refunds need a look"

Paddle's settings in `.env` make selling *possible*; this makes it *open*. **Sales start paused**
— the first deploy with the keys takes no money until somebody runs `sales open`. Pausing stops new
checkouts only: a purchase already paid for is still credited when Paddle's webhook arrives.
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
from chessmark.core.sales import Sales  # noqa: E402


def _ago(at: dt.datetime) -> str:
    seconds = int((dt.datetime.now(dt.UTC) - at).total_seconds())
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    return f"{seconds / 3600:.1f}h ago"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["open", "pause"])
    parser.add_argument("reason", nargs="?", help="why you are pausing")
    args = parser.parse_args()
    if args.action == "pause" and not args.reason:
        parser.error('say why: ./chessmark sales pause "reason"')
    if args.action == "open" and args.reason:
        parser.error("open takes no reason")

    settings = get_settings()
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    sales = Sales(redis)
    try:
        if args.action == "open":
            await sales.open()
            print("credit sales open")
            if not settings.selling_credit:
                # Open is recorded anyway, so the order of the two steps does not matter; but say
                # plainly that nothing can be bought yet, rather than let "open" imply it can.
                print("but Paddle is not configured on this server, so nothing can be bought yet")
            return 0
        if args.action == "pause":
            await sales.pause(args.reason)
            print(f"credit sales paused — {args.reason}")
            print("purchases already paid for are still credited; reopen with ./chessmark sales open")
            return 0

        state = await sales.state()
        when = f" {_ago(state.at)}" if state.at else ""
        print(f"open{when}" if state.open else f"paused{when}: {state.reason}")
        if not settings.selling_credit:
            print("Paddle is not configured on this server: nothing can be bought either way")
        return 0
    finally:
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

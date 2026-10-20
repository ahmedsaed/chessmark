"""Pick the game the lobby shows when nothing is live (ADR-0070).

The same thing `POST /admin/featured` does, without needing an admin session — on a server the
person choosing is at a shell, not signed in to their own site.

    feature_game.py <game> "headline" --white-quote "..." --black-quote "..."
    feature_game.py --list
    feature_game.py --remove <game>

`<game>` is the game's URL, its id, or the first eight characters of its id — whatever is to hand.
Each quote is optional, and each is refused unless it is word for word from that side's own
reasoning or notes in that game: copy it from the replay. The newest pick is the one shown.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from chessmark.db import featured  # noqa: E402
from chessmark.db.models import FeaturedGame, Game  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402
from chessmark.orchestration.revalidation import FEATURED, notify_tags  # noqa: E402

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN = "\033[31m", "\033[32m"

_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8,}", re.IGNORECASE)


async def resolve(session: AsyncSession, ref: str) -> uuid.UUID | None:
    """A game from its URL, its id, or a prefix of it — refusing a prefix that names two."""
    found = _ID.search(ref.rsplit("/", 1)[-1])
    if found is None:
        return None
    text = found.group(0).lower()
    try:
        return uuid.UUID(text)
    except ValueError:
        pass
    ids = (
        await session.scalars(
            sa.select(Game.id).where(sa.cast(Game.id, sa.Text).like(f"{text}%")).limit(2)
        )
    ).all()
    return ids[0] if len(ids) == 1 else None


def show(pick: FeaturedGame) -> str:
    when = pick.featured_at.strftime("%Y-%m-%d %H:%M")
    state = f"{RED}removed{OFF}" if pick.removed_at else f"{GREEN}standing{OFF}"
    lines = [f"{DIM}{when}{OFF} {state} {BOLD}{pick.headline}{OFF}  {DIM}{pick.game_id}{OFF}"]
    for colour, quote in (("white", pick.white_quote), ("black", pick.black_quote)):
        if quote:
            lines.append(
                f"    {colour} {DIM}ply {quote['ply']} {quote['san']}{OFF} “{quote['text']}”"
            )
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("game", nargs="?", help="the game's URL, id, or first eight characters")
    parser.add_argument("headline", nargs="?", help="one line of your own about the game")
    parser.add_argument("--white-quote", help="word for word from White's reasoning or notes")
    parser.add_argument("--black-quote", help="word for word from Black's reasoning or notes")
    parser.add_argument("--list", action="store_true", help="every pick, newest first")
    parser.add_argument("--remove", metavar="GAME", help="take a game out of the hero")
    args = parser.parse_args()

    try:
        async with session_scope() as session:
            if args.list:
                picks = await featured.history(session)
                for pick in picks:
                    print(show(pick))
                if not picks:
                    print(f"{DIM}nothing has been featured{OFF}")
                return 0

            ref = args.remove or args.game
            if ref is None:
                parser.error("name a game, or --list")
            game_id = await resolve(session, ref)
            if game_id is None:
                print(f"{RED}no single game matches {ref!r}{OFF}", file=sys.stderr)
                return 1

            if args.remove:
                if not await featured.unfeature(session, game_id):
                    print(f"{RED}that game is not featured{OFF}", file=sys.stderr)
                    return 1
                message = f"{GREEN}removed{OFF} {DIM}{game_id}{OFF}"
            else:
                if not args.headline:
                    parser.error("a featured game needs a headline")
                try:
                    pick = await featured.feature(
                        session,
                        game_id,
                        featured.Pick(args.headline, args.white_quote, args.black_quote),
                    )
                except featured.FeatureRefusedError as refused:
                    print(f"{RED}{refused}{OFF}", file=sys.stderr)
                    return 1
                message = f"{GREEN}featured{OFF}\n{show(pick)}"

        # After the commit `session_scope` made, never inside it: a cache hint must not be able to
        # roll back the decision it is about.
        await notify_tags([FEATURED])
        print(message)
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

#!/usr/bin/env python3
"""Compute the digest of every finished game that lacks one, or has one from an older version.

    make backfill-digests
    ./chessmark digests                 # on the server

A game's digest is computed on the first read of its page anyway (ADR-0069). This does the whole
archive at once, so the first crawler to reach an old game is not the one that pays for it, and so
the model pages' openings count every game rather than only the ones somebody has opened.

Safe to re-run and to interrupt: each game is written on its own, a game already at the current
`DIGEST_VERSION` is skipped, and a digest is derived from the record alone, so recomputing one
cannot change anything but the digest.
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

from chessmark.db.digests import DIGEST_VERSION, compute, store  # noqa: E402
from chessmark.db.enums import GameStatus  # noqa: E402
from chessmark.db.models import Game  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="count what would be computed, and write nothing"
    )
    args = parser.parse_args()

    try:
        async with session_scope() as session:
            ids = list(
                await session.scalars(
                    sa.select(Game.id)
                    .where(
                        Game.status.in_((GameStatus.FINISHED, GameStatus.ABORTED)),
                        sa.or_(
                            Game.digest.is_(None),
                            Game.digest["version"].as_integer() != DIGEST_VERSION,
                        ),
                    )
                    .order_by(Game.ended_at.desc().nulls_last())
                )
            )
            print(f"{len(ids)} game(s) without a current digest (version {DIGEST_VERSION})")
            if args.dry_run:
                print("dry run — nothing written")
                return 0

            for index, game_id in enumerate(ids, start=1):
                game = await session.get(Game, game_id)
                assert game is not None
                await store(session, game.id, await compute(session, game))
                # Each game's events are read in full; let them go before the next one.
                session.expunge_all()
                if index % 50 == 0 or index == len(ids):
                    print(f"  {index}/{len(ids)}")
            await session.rollback()
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

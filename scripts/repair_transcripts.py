#!/usr/bin/env python3
"""Supersede transcript rows no provider will accept (ADR-0021).

    make repair-transcripts            # report what it would do
    make repair-transcripts ARGS=--write

One rule, and it is the one Liquid enforces: an assistant message needs `content`, `tool_calls` or
`function_call`. A row with none of them renders as a bare `{"role": "assistant"}` and is refused
with a 400 naming `messages.<n>.content`. The transcript is append-only (ADR-0003), so a single such
row refuses **every later turn of that seat** — the one 400 that no pause, retry or resume can clear.
It abandoned a real game at ply 57.

`turn.py` no longer writes the row and `compaction.is_sendable` filters it on the way out, so a game
started after that change cannot have one and a game that does can still play. This clears the
record itself, so the filter is a second line rather than the only one.

**Superseded, never deleted**, exactly as compaction folds a row: `superseded_at` is set, the row
keeps its place and its `seq`, and the request stops carrying it. Invariant 3 asks that the record
be verbatim and it still is — `full_history` and `llm_calls` are untouched. Nothing can be orphaned
by this, because a row with no `tool_calls` has no `tool` results depending on it.
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

import sqlalchemy as sa  # noqa: E402

from chessmark.db.models import Game, Player, TranscriptMessage  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402


def _unsendable() -> sa.ColumnElement[bool]:
    """The rows this script exists for.

    Mirrors `compaction.is_sendable`, inverted. An empty *string* counts as content — it is the
    model having said nothing rather than the column being absent — so only `NULL` and `''` with no
    tool calls qualify, which is what the provider's check sees as missing.
    """
    return sa.and_(
        TranscriptMessage.role == "assistant",
        TranscriptMessage.superseded_at.is_(None),
        sa.or_(TranscriptMessage.content.is_(None), TranscriptMessage.content == ""),
        sa.or_(
            TranscriptMessage.tool_calls.is_(None),
            sa.cast(TranscriptMessage.tool_calls, sa.Text) == "[]",
        ),
    )


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
                    TranscriptMessage.id,
                    TranscriptMessage.seq,
                    TranscriptMessage.game_id,
                    Player.colour,
                    Player.display_name,
                    Game.status,
                )
                .join(Player, Player.id == TranscriptMessage.player_id)
                .join(Game, Game.id == TranscriptMessage.game_id)
                .where(_unsendable())
                .order_by(TranscriptMessage.game_id, TranscriptMessage.seq)
            )
            if args.game:
                query = query.where(TranscriptMessage.game_id == args.game)

            rows = list((await session.execute(query)).all())

            if not rows:
                print("no unsendable transcript rows")
                return 0

            for row in rows:
                print(
                    f"{row.game_id} seq {row.seq}: {row.colour.value} ({row.display_name}) "
                    f"— game is {row.status.value}"
                )
            seats = {(row.game_id, row.colour) for row in rows}
            print(f"\n{len(rows)} rows across {len(seats)} seats")

            if not args.write:
                print("dry run; pass --write to supersede them")
                return 0

            await session.execute(
                sa.update(TranscriptMessage)
                .where(TranscriptMessage.id.in_([row.id for row in rows]))
                .values(superseded_at=dt.datetime.now(dt.UTC))
            )
            print(f"superseded {len(rows)} rows")
            print("a game abandoned on this can now be reopened: make resume GAME=<id>")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

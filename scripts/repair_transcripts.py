#!/usr/bin/env python3
"""Supersede transcript rows no provider will accept (ADR-0021).

    make repair-transcripts            # report what it would do
    make repair-transcripts ARGS=--write

**Two rules**, and both describe a row that makes every later turn of a seat impossible. The
transcript is append-only (ADR-0003), so one of them is terminal: no pause, retry or resume clears
it, and the seat is finished for the rest of the game.

The first is the one Liquid enforces: an assistant message needs `content`, `tool_calls` or
`function_call`. A row with none of them renders as a bare `{"role": "assistant"}` and is refused
with a 400 naming `messages.<n>.content`. The transcript is append-only (ADR-0003), so a single such
row refuses **every later turn of that seat** — the one 400 that no pause, retry or resume can clear.
It abandoned a real game at ply 57.

`turn.py` no longer writes the row and `compaction.is_sendable` filters it on the way out, so a game
started after that change cannot have one and a game that does can still play. This clears the
record itself, so the filter is a second line rather than the only one.

The second is universal, and it is the mirror image: an assistant row **with** `tool_calls` that
nothing answered. Every provider refuses it —

    TOOL_CALLS_MISSING_RESULTS: An assistant message with 'tool_calls' must be followed by tool
    results

— and it is written whenever a turn ends between appending that message and running its calls.
`max_closing_rounds` did exactly that for two days (ADR-0037), and it abandoned `a2e44449` at ply
2: White moved, spent its closing rounds reading the board, asked for one more tool as the bound
was reached, and the turn returned without answering it.

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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.db.models import Game, Player, TranscriptMessage  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402

#: How an *absent* `tool_calls` is actually stored, and it is not what you would guess.
#:
#: `tool_calls` is `JSONB`, and SQLAlchemy writes a Python `None` into a JSON column as the JSON
#: value `null` — `'null'::jsonb` — rather than as SQL `NULL`. So `tool_calls IS NULL` matches
#: **nothing**: against a database holding 373 assistant rows it returned zero, and this script
#: reported "no unsendable transcript rows" for a transcript that had abandoned a game.
#:
#: Python-side code never noticed, because JSONB `null` deserialises back to `None` and
#: `compaction.is_sendable` reads the attribute rather than querying — which is why the runtime
#: filter worked while the repair query silently found nothing. A repair that finds nothing reads
#: as success, which makes this the worst shape of bug to leave untested.
_NO_TOOL_CALLS = ("null", "[]")


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
            sa.cast(TranscriptMessage.tool_calls, sa.Text).in_(_NO_TOOL_CALLS),
        ),
    )


def _unanswered() -> sa.ColumnElement[bool]:
    """Assistant rows whose tool calls nothing answered.

    A correlated `NOT EXISTS` over the seat's own `tool` rows rather than a join, because a row is
    unanswered only when *no* result names any of its calls — and a join would report the row once
    per call and miss the distinction between "one of three answered" and "none". Comparing the
    whole `tool_calls` array is not possible in SQL here, so the test is per id, expanded in
    Python by `_ids_without_results`.
    """
    return sa.and_(
        TranscriptMessage.role == "assistant",
        TranscriptMessage.superseded_at.is_(None),
        TranscriptMessage.tool_calls.is_not(None),
        sa.cast(TranscriptMessage.tool_calls, sa.Text).not_in(_NO_TOOL_CALLS),
    )


async def _ids_without_results(session: Any, game: str | None) -> set[int]:
    """The ids of assistant rows carrying at least one unanswered `tool_call_id`.

    Read in Python because the ids live inside a JSONB array: a row is broken if *any* of its
    calls is unanswered, which is what the provider checks.
    """
    query = (
        sa.select(TranscriptMessage.id, TranscriptMessage.player_id, TranscriptMessage.tool_calls)
        .where(_unanswered())
        .order_by(TranscriptMessage.seq)
    )
    if game:
        query = query.where(TranscriptMessage.game_id == game)
    rows = list((await session.execute(query)).all())
    if not rows:
        return set()

    seats = {row.player_id for row in rows}
    answered = {
        (player_id, tool_call_id)
        for player_id, tool_call_id in (
            await session.execute(
                sa.select(TranscriptMessage.player_id, TranscriptMessage.tool_call_id).where(
                    TranscriptMessage.player_id.in_(seats),
                    TranscriptMessage.role == "tool",
                    TranscriptMessage.tool_call_id.is_not(None),
                )
            )
        ).all()
    }

    return {
        row.id
        for row in rows
        if any((row.player_id, call.get("id")) not in answered for call in (row.tool_calls or []))
    }


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
            unanswered = await _ids_without_results(session, args.game)
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
                .where(sa.or_(_unsendable(), TranscriptMessage.id.in_(unanswered)))
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

"""The repair script's query actually finds the rows it exists for (ADR-0021).

Written after `./chessmark repair` reported "no unsendable transcript rows" on a database known to
contain one. The script shipped without a test over its predicate, which is the gap that lets a
query be plausible and wrong at the same time — and a repair that silently finds nothing is worse
than one that fails, because it reads as success.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import transcript
from chessmark.db.models import TranscriptMessage
from tests.agents.conftest import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_repair = importlib.import_module("repair_transcripts")
_unsendable = _repair._unsendable
_ids_without_results = _repair._ids_without_results

pytestmark = pytest.mark.integration


async def _ids(db: AsyncSession) -> list[int]:
    return list(await db.scalars(sa.select(TranscriptMessage.id).where(_unsendable())))


async def _row(db: AsyncSession, table: Table, **kwargs: Any) -> TranscriptMessage:
    row = await transcript.append_message(
        db, player_id=table.white.id, game_id=table.game.id, role="assistant", **kwargs
    )
    await db.flush()
    return row


async def test_a_null_content_row_is_found(db: AsyncSession, table: Table) -> None:
    """The shape that abandoned the real game: `content` NULL, `tool_calls` NULL."""
    row = await _row(db, table, content=None, tool_calls=None)

    assert await _ids(db) == [row.id]


async def test_an_empty_string_row_is_found(db: AsyncSession, table: Table) -> None:
    row = await _row(db, table, content="", tool_calls=None)

    assert await _ids(db) == [row.id]


async def test_an_empty_tool_call_list_is_found(db: AsyncSession, table: Table) -> None:
    """`[]` is stored as JSONB, so it is not NULL and the cast is what catches it."""
    row = await _row(db, table, content=None, tool_calls=[])

    assert await _ids(db) == [row.id]


async def test_a_reasoning_only_row_is_found(db: AsyncSession, table: Table) -> None:
    """`reasoning_details` alone still renders as an assistant message with no content and no
    tool calls, which is exactly what the provider refuses."""
    row = await _row(db, table, content=None, reasoning_details=[{"type": "reasoning.text"}])

    assert await _ids(db) == [row.id]


async def test_rows_that_are_fine_are_left_alone(db: AsyncSession, table: Table) -> None:
    await _row(db, table, content="I will play e4")
    await _row(db, table, content=None, tool_calls=[{"id": "c", "type": "function"}])
    await transcript.append_message(
        db, player_id=table.white.id, game_id=table.game.id, role="user", content=""
    )
    await db.flush()

    assert await _ids(db) == []


async def test_an_already_superseded_row_is_not_offered_again(
    db: AsyncSession, table: Table
) -> None:
    """Otherwise a second run reports work it already did."""
    row = await _row(db, table, content=None, tool_calls=None)
    row.superseded_at = sa.func.now()
    await db.flush()

    assert await _ids(db) == []


# ====================================================================== unanswered tool calls


async def _unanswered_ids(db: AsyncSession, game: str | None = None) -> set[int]:
    return await _ids_without_results(db, game)


class TestAnAssistantRowNothingAnswered:
    """The mirror image of the rule above, and the one that abandoned `a2e44449` at ply 2.

    Every provider refuses an assistant message whose `tool_calls` nothing answered —
    *"TOOL_CALLS_MISSING_RESULTS: An assistant message with 'tool_calls' must be followed by tool
    results"* — and the transcript is append-only, so one such row finishes that seat for the rest
    of the game. `max_closing_rounds` wrote them for two days (ADR-0037).
    """

    async def test_a_call_with_no_result_is_found(self, db: AsyncSession, table: Table) -> None:
        row = await _row(
            db,
            table,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_board", "arguments": "{}"},
                }
            ],
        )

        assert row.id in await _unanswered_ids(db)

    async def test_a_call_with_its_result_is_not(self, db: AsyncSession, table: Table) -> None:
        """The overwhelming majority of assistant rows. A rule that caught these would supersede a
        working transcript and break the game it was run to repair."""
        row = await _row(
            db,
            table,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_board", "arguments": "{}"},
                }
            ],
        )
        await transcript.append_message(
            db,
            player_id=table.white.id,
            game_id=table.game.id,
            role="tool",
            content="{}",
            tool_call_id="c1",
        )
        await db.flush()

        assert row.id not in await _unanswered_ids(db)

    async def test_one_unanswered_call_among_several_is_enough(
        self, db: AsyncSession, table: Table
    ) -> None:
        """A response may carry several calls, and the provider requires a result for **each**. A
        rule reading "any answered" would leave the row that still refuses every later turn."""
        row = await _row(
            db,
            table,
            tool_calls=[
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "get_board", "arguments": "{}"},
                },
                {"id": "b", "type": "function", "function": {"name": "say", "arguments": "{}"}},
            ],
        )
        await transcript.append_message(
            db,
            player_id=table.white.id,
            game_id=table.game.id,
            role="tool",
            content="{}",
            tool_call_id="a",
        )
        await db.flush()

        assert row.id in await _unanswered_ids(db)

    async def test_another_seat_s_result_does_not_count(
        self, db: AsyncSession, table: Table
    ) -> None:
        """Tool-call ids are the provider's and are not unique across seats — `a2e44449` carried
        `get_board_6qd71hvdfemd` on one side and `call_868e...` on the other, but a scripted or
        retried game can repeat one. Matching on the id alone would call a broken transcript whole
        because the *opponent* answered a call of the same name."""
        row = await _row(
            db,
            table,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_board", "arguments": "{}"},
                }
            ],
        )
        await transcript.append_message(
            db,
            player_id=table.black.id,
            game_id=table.game.id,
            role="tool",
            content="{}",
            tool_call_id="c1",
        )
        await db.flush()

        assert row.id in await _unanswered_ids(db)

    async def test_a_superseded_row_is_left_alone(self, db: AsyncSession, table: Table) -> None:
        """Running the repair twice must be a no-op, not a second pass over rows already folded."""
        import datetime as dt

        row = await _row(
            db,
            table,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_board", "arguments": "{}"},
                }
            ],
        )
        row.superseded_at = dt.datetime.now(dt.UTC)
        await db.flush()

        assert row.id not in await _unanswered_ids(db)

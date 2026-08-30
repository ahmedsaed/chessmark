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
_unsendable = importlib.import_module("repair_transcripts")._unsendable

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

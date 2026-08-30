"""A transcript never holds a message a provider will refuse (ADR-0021).

A completion with neither content nor tool calls used to append an assistant row with both columns
null, which renders as a bare `{"role": "assistant"}`. Liquid answers that with a 400 —
*"Assistant messages require `content`, `tool_calls`, or `function_call`"*, naming
`messages.126.content` — and the transcript is append-only, so the row refuses **every later turn of
that seat**. It abandoned a real game at ply 57, and it is the one 400 no pause, retry or resume can
clear.

Two halves, because the fix has two: the row is no longer written, and a transcript that already
holds one still builds a sendable request.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import transcript
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.models import TranscriptMessage
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration


def _refusable(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Assistant messages carrying neither `content` nor `tool_calls`.

    Exactly the check Liquid applies. `content` must be *present*, so a missing key counts — that
    is what `to_provider_message` emits for a null column, and what the provider named.
    """
    return [
        message
        for message in messages
        if message.get("role") == "assistant"
        and "content" not in message
        and "tool_calls" not in message
    ]


async def test_a_silent_completion_writes_no_assistant_row(db: AsyncSession, table: Table) -> None:
    """The model said nothing and did nothing, so there is nothing to replay."""
    await play_turn(
        db,
        table,
        scripted(
            step(),  # no content, no tool calls — an ordinary truncation
            step(tool_call("make_move", move="e4")),
        ),
    )

    rows = list(
        await db.scalars(
            sa.select(TranscriptMessage)
            .where(TranscriptMessage.player_id == table.white.id)
            .order_by(TranscriptMessage.seq)
        )
    )
    empty = [r for r in rows if r.role == "assistant" and not r.content and not r.tool_calls]
    assert empty == [], "an assistant row with nothing in it poisons every later turn of this seat"

    assert _refusable(await transcript.build_messages(db, table.white.id)) == []


async def test_the_turn_still_completes_after_a_silent_completion(
    db: AsyncSession, table: Table
) -> None:
    """Dropping the row must not break the loop it sits in.

    The nudge still lands, the model still gets to act, and the two consecutive user messages that
    result are accepted everywhere.
    """
    result = await play_turn(
        db,
        table,
        scripted(step(), step(tool_call("make_move", move="e4"))),
    )

    assert result.moved
    assert result.move is not None
    assert result.move.move.san == "e4"


async def test_a_transcript_that_already_holds_one_still_builds_a_request(
    db: AsyncSession, table: Table
) -> None:
    """The second line of defence, for the seats already carrying one.

    Written directly rather than through a turn, because the turn can no longer produce it — which
    is the point of the first test and the reason this one has to forge the row.
    """
    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))))

    await transcript.append_message(
        db,
        player_id=table.white.id,
        game_id=table.game.id,
        role="assistant",
        content=None,
        tool_calls=None,
    )
    await db.flush()

    messages = await transcript.build_messages(db, table.white.id)
    assert _refusable(messages) == [], "a poisoned row must not reach the provider"

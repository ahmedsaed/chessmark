"""Assistant prose reaches the event stream.

The first paid benchmark made this visible: Gemini produced `content` on 43 of its 83 calls and
`reasoning` on none, DeepSeek the exact reverse. The conversation panel rendered only `reasoning`,
so one of the two models appeared to play in total silence for eighty plies.

Providers genuinely differ here, so both channels have to be carried — and kept apart, because
"what the model said" and "what the model was thinking" are different things to a reader.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.enums import EventType
from chessmark.db.models import GameEvent
from tests.orchestration.conftest import Fixture, run_next

pytestmark = pytest.mark.integration


async def _events(db: AsyncSession, game_id: Any, type_: EventType) -> list[dict[str, Any]]:
    db.expunge_all()
    rows = await db.scalars(
        sa.select(GameEvent).where(GameEvent.game_id == game_id, GameEvent.type == type_)
    )
    return [row.payload for row in rows]


async def test_content_reaches_the_stream(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """A model that talks through `content` must not read as silent."""
    worker = make_worker(
        scripted(step(tool_call("make_move", move="e4"), content="I will play the Italian Game."))
    )
    await run_next(worker, game.queue)

    payloads = await _events(db, game.game.id, EventType.OUTPUT)

    assert [p["content"] for p in payloads] == ["I will play the Italian Game."]


async def test_reasoning_and_content_are_separate_events(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """Collapsing them would either hide half the models or mislabel the other half."""
    worker = make_worker(
        scripted(
            step(
                tool_call("make_move", move="e4"),
                content="Italian Game.",
                reasoning="Center control first.",
            )
        )
    )
    await run_next(worker, game.queue)

    assert [p["content"] for p in await _events(db, game.game.id, EventType.OUTPUT)] == [
        "Italian Game."
    ]
    assert [p["reasoning"] for p in await _events(db, game.game.id, EventType.THINKING)] == [
        "Center control first."
    ]


async def test_empty_content_emits_nothing(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """Most tool-calling replies have `content: null`. An event per empty string would bury the
    stream in noise."""
    worker = make_worker(scripted(step(tool_call("make_move", move="e4"), content="   ")))
    await run_next(worker, game.queue)

    assert await _events(db, game.game.id, EventType.OUTPUT) == []


async def test_a_tool_call_event_carries_its_arguments_and_result(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """ "`get_legal_moves()` succeeded" tells a reader nothing. What was asked and what came back is
    the part worth showing."""
    worker = make_worker(
        scripted(step(tool_call("get_legal_moves"), tool_call("make_move", move="e4")))
    )
    await run_next(worker, game.queue)

    payloads = await _events(db, game.game.id, EventType.TOOL_CALLED)
    by_tool = {p["tool"]: p for p in payloads}

    assert by_tool["make_move"]["args"] == {"move": "e4"}
    assert by_tool["get_legal_moves"]["result"]["moves"]


async def test_an_illegal_attempt_still_carries_the_legal_moves(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """Invariant 6 on the display side: the reader should see the list the model was handed."""
    worker = make_worker(
        scripted(step(tool_call("make_move", move="Ke4"), tool_call("make_move", move="e4")))
    )
    await run_next(worker, game.queue)

    payloads = await _events(db, game.game.id, EventType.ILLEGAL_ATTEMPT)

    assert payloads[0]["result"]["legal_moves_san"]

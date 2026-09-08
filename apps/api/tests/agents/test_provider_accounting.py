"""An endpoint that cannot count its own prompt ends the game (ADR-0033).

A call that **succeeded** necessarily fit inside the window, and we know what output we asked it to
reserve — so `prompt + our ask` cannot exceed the context length. When the report says otherwise,
the report is wrong.

It is wrong in the direction that wedges a game. The figure is carried to the next turn, every
calculation from it concludes there is no room, and the seat gives up before making a call:
`29e7f004` died reporting *"a 256000-token window holding a 549680-token prompt"* having never
reached the provider. `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` produced 24 such reports
in 339 calls, worst case claiming 516,877 tokens in a 256,000-token window. Eleven other models
produced none in roughly 7,800 calls.

There is no honest recovery. Every model tokenises differently, OpenRouter exposes no counting
endpoint, and a local estimate is exactly what AGENT-19 forbids on this path — so the provider's
numbers are the only ones available, and this provider's are not numbers. The game is abandoned
and the endpoint is named.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.turn import TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import ModelEndpoint, ModelRegistry
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration

CONTEXT = 256_000
MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"


async def _register(db: AsyncSession) -> None:
    await sync_model_registry(
        db, [{"openrouter_id": MODEL, "display_name": MODEL, "context_length": CONTEXT}]
    )
    await db.flush()
    model_id = await db.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == MODEL)
    )
    db.add(
        ModelEndpoint(
            model_id=model_id,
            provider_name="TestProvider",
            context_length=CONTEXT,
            supports_tools=True,
            is_active=True,
        )
    )
    await db.commit()


async def test_an_impossible_report_ends_the_game(db: AsyncSession, table: Table) -> None:
    """The real shape: a call that worked, reporting a prompt larger than the window it fitted in."""
    await _register(db)

    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=516_877)),
        model=MODEL,
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.FAILED
    assert result.request_rejected, "retrying sends the same bytes and gets the same nonsense back"
    assert "token accounting is wrong" in (result.error or "")
    assert MODEL in (result.error or ""), "the endpoint is named, because it is the finding"


async def test_nobody_is_forfeited_for_it(db: AsyncSession, table: Table) -> None:
    """Invariant 11. The model played a perfectly good move; its host cannot add up."""
    await _register(db)

    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=516_877)),
        model=MODEL,
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.outcome is None


async def test_a_prompt_that_merely_fills_the_window_is_fine(
    db: AsyncSession, table: Table
) -> None:
    """**The line is impossibility, not size.** A large prompt is ordinary at ply 70; the refusal is
    reserved for arithmetic that cannot be true, so a game near its window is never touched.
    """
    await _register(db)
    asked = 20_000

    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=CONTEXT - asked - 1)),
        model=MODEL,
        limits=TurnLimits(max_completion_tokens=asked),
    )

    assert result.status is TurnStatus.COMPLETED


async def test_an_unknown_window_cannot_convict(db: AsyncSession, table: Table) -> None:
    """With no context length there is nothing to contradict, and an unregistered model still
    plays. Silence is not evidence."""
    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=900_000)),
        model="scripted/unregistered",
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.COMPLETED

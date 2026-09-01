"""The prompt size is measured, never estimated (AGENT-19, ADR-0021).

The estimate was documented as running only before a turn's first response, and actually ran before
*every* turn's first call: the worker builds a new `TurnRunner` each turn, so the counter holding
the measurement started at zero every time. It reported 477,155 tokens for a six-ply transcript
against a 256,000-token window; `completion_cap`'s `max(1, ...)` floor then asked the endpoint for
one output token, every reply came back `finish_reason: "length"`, and the game ended `truncated`,
`1-0`, against a model that had done nothing wrong.

Two properties keep that shut: the measurement survives the turn boundary, and a window with no
room raises instead of clamping to a token nobody can answer in.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import compaction
from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.turn import TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import LlmCall, ModelEndpoint, ModelRegistry
from chessmark.game import Colour
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration


async def _register(
    db: AsyncSession, *, slug: str, context: int, max_completion: int | None = None
) -> None:
    await sync_model_registry(
        db, [{"openrouter_id": slug, "display_name": slug, "context_length": context}]
    )
    await db.flush()
    model_id = await db.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slug)
    )
    db.add(
        ModelEndpoint(
            model_id=model_id,
            provider_name="TestProvider",
            context_length=context,
            max_completion_tokens=max_completion,
            supports_tools=True,
            is_active=True,
        )
    )
    await db.commit()


async def _max_tokens_asked(db: AsyncSession) -> list[int | None]:
    """What each recorded request actually asked the endpoint for.

    Read from `llm_calls` rather than from the scripted double, because the request we *stored* is
    the one a reader would audit — and a `max_tokens` of 1 in there is the whole bug.
    """
    calls = list(await db.scalars(sa.select(LlmCall).order_by(LlmCall.id)))
    return [call.request.get("max_tokens") for call in calls]


async def test_the_measurement_survives_the_turn_boundary(db: AsyncSession, table: Table) -> None:
    """The bug: a new runner per turn meant the count was thrown away between turns."""
    slug = "scripted/measured"
    await _register(db, slug=slug, context=200_000)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=12_345)),
        model=slug,
        colour=Colour.WHITE,
    )

    await db.refresh(table.white)
    assert table.white.last_prompt_tokens == 12_345, (
        "the seat must carry what the provider reported, or the next turn has nothing to go on"
    )


async def test_the_next_turn_asks_against_the_measured_size(db: AsyncSession, table: Table) -> None:
    """`context - measured - 256`, not `context - a guess - 256`."""
    slug = "scripted/measured-two"
    await _register(db, slug=slug, context=100_000)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=40_000)),
        model=slug,
        colour=Colour.WHITE,
    )
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )
    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="d4"), prompt_tokens=41_000)),
        model=slug,
        colour=Colour.WHITE,
    )

    asked = await _max_tokens_asked(db)
    assert asked[0] == 50_000, "a game's first call is bounded at half the window, not estimated"
    assert asked[-1] == 100_000 - 40_000 - 256, "and every later one is measured"


async def test_a_full_window_fails_the_turn_instead_of_forfeiting_the_model(
    db: AsyncSession, table: Table
) -> None:
    """Invariant 11. The old floor asked for one token and called the result a model failure.

    Nothing is compactable here — the transcript is one system prompt — so the ladder has no rung
    to climb and the honest answer is that the harness cannot send this request. That is a failed
    turn for the worker to decide about, never a `truncated` forfeit.
    """
    slug = "scripted/cramped"
    await _register(db, slug=slug, context=4_000)
    table.white.last_prompt_tokens = 3_900
    await db.commit()

    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"))),
        model=slug,
        limits=TurnLimits(context_reserve_tokens=100),
    )

    assert result.status is TurnStatus.FAILED
    assert result.outcome is None, "a harness bound is never a finding about a player (ADR-0019)"
    assert "leaves" in (result.error or ""), result.error
    assert await _max_tokens_asked(db) == [], "and no doomed request was sent"


async def test_we_never_ask_for_more_output_than_the_endpoint_will_give(
    db: AsyncSession, table: Table
) -> None:
    """The window is not the only ceiling, and the other one had never been read (ADR-0024).

    Poolside serves `laguna-s-2.1` in a 256,000-token window and stops at 32,768 output tokens. We
    asked for 64,000, so every long answer came back at `finish_reason: "length"` having stopped
    short of what we asked — which is precisely the signature `_our_ceiling_bound` reads as "the
    endpoint cut it, so the model could not finish". A game won by rook and two bishops against a
    lone pawn was scored a loss on it.

    Asking for what the endpoint will actually give is what closes that: the response then stops at
    *our* number, which the harness already knew not to blame anybody for.
    """
    slug = "scripted/capped-output"
    await _register(db, slug=slug, context=256_000, max_completion=32_768)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=34_000)),
        model=slug,
        colour=Colour.WHITE,
    )

    assert await _max_tokens_asked(db) == [32_768], (
        "half of a 256,000-token window is 128,000, and the endpoint would never have emitted it"
    )


async def test_an_endpoint_that_declares_no_output_ceiling_is_unaffected(
    db: AsyncSession, table: Table
) -> None:
    """Unknown is not a number to clamp with, so the request goes out exactly as it always did."""
    slug = "scripted/uncapped-output"
    await _register(db, slug=slug, context=256_000, max_completion=None)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"))),
        model=slug,
        colour=Colour.WHITE,
    )

    assert await _max_tokens_asked(db) == [64_000], (
        "the full request, bounded only by half the window — which is larger here"
    )


async def test_the_endpoints_ceiling_reaches_the_window(db: AsyncSession) -> None:
    """`window_for` reads both columns off the one row it already fetches."""
    slug = "scripted/window-read"
    await _register(db, slug=slug, context=256_000, max_completion=32_768)

    window = await compaction.window_for(db, model_slug=slug, provider="TestProvider")

    assert window.context == 256_000
    assert window.max_completion == 32_768


def test_the_estimate_is_gone() -> None:
    """Deleted rather than calibrated: it sat on the path where a wrong answer forfeits a player,
    and the exact number is in our hands one call later."""
    assert not hasattr(compaction, "estimate_tokens")
    assert not hasattr(compaction, "CHARS_PER_TOKEN")


def test_an_unmeasured_first_call_is_bounded(_: Any = None) -> None:
    window = compaction.Window(context=65_536)

    assert window.completion_cap(None, 64_000) == 32_768

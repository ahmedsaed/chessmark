"""Compaction trims before it summarises, and verifies it did something (AGENT-20, ADR-0021).

Game `29e7f004` compacted five times and was abandoned on a context-length 400 anyway. The event
log says why: it folded 26, then 7, then 3, then 17, then 4 messages while keeping 29, 26, 41, 41
and 50 — because `keep_turns=4` of a reasoning model is fifty messages, larger than the window they
were supposed to fit inside. `Plan.worthwhile` asked `bool(self.fold)`, so folding 3 of 44 counted
as success and the loop believed it had made room.

Three properties close that: the retained turns are bounded in messages as well as turns, stale
tool output is elided without a provider call, and a refusal for size compacts against the
provider's own numbers and retries instead of abandoning the game.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import compaction, transcript
from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import prose, scripted, step, tool_call
from chessmark.agents.turn import TurnLimits
from chessmark.db.enums import EventType, TurnStatus
from chessmark.db.models import GameEvent, ModelEndpoint, ModelRegistry, TranscriptMessage
from chessmark.db.models import Turn as TurnRow
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration

#: The refusal that abandoned the real game, verbatim apart from the wrapping.
CONTEXT_400 = (
    "litellm.BadRequestError: OpenrouterException - "
    '{"error":{"message":"This endpoint\'s maximum context length is 256000 tokens. '
    "However, you requested about 262254 tokens (261751 of text input, 502 of tool input, "
    '1 in the output).","code":400,"metadata":{"provider_name":null}}}'
)


class Refuses:
    """A provider that refuses the first call for size, then answers.

    Written as a class rather than a closure so the test can assert on how many calls it saw: the
    point of the reactive rung is that the *second* call succeeds, not that the error is swallowed.
    """

    def __init__(self, *after: dict[str, Any]) -> None:
        self.after = list(after)
        self.calls = 0

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise _BadRequestError(CONTEXT_400)
        return self.after[min(self.calls - 2, len(self.after) - 1)]


class _BadRequestError(Exception):
    status_code = 400


async def _register(db: AsyncSession, *, slug: str, context: int) -> None:
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
            supports_tools=True,
            is_active=True,
        )
    )
    await db.commit()


async def _history(db: AsyncSession, table: Table, *, turns: int, measured: int = 0) -> None:
    """A transcript of complete turns, each with a fat tool result — a chess transcript's shape.

    `get_legal_moves` returns 38 or 39 move objects and a turn calls it most plies, so by the
    midgame the request is mostly enumerations of positions that no longer exist. That is what
    rung one exists to drop.
    """
    seq = 1
    db.add(
        TranscriptMessage(
            game_id=table.game.id,
            player_id=table.white.id,
            seq=seq,
            role="system",
            content="You are playing chess.",
        )
    )
    for index in range(turns):
        row = TurnRow(game_id=table.game.id, player_id=table.white.id)
        db.add(row)
        await db.flush()
        for role in ("user", "assistant", "tool", "assistant"):
            seq += 1
            db.add(
                TranscriptMessage(
                    game_id=table.game.id,
                    player_id=table.white.id,
                    seq=seq,
                    turn_id=row.id,
                    role=role,
                    tool_call_id=f"call_{seq}" if role == "tool" else None,
                    name="get_legal_moves" if role == "tool" else None,
                    content=(
                        ("legal moves: " + "Nf3 Nc3 e4 d4 " * 200)
                        if role == "tool"
                        else f"{role} on turn {index}"
                    ),
                )
            )
    table.white.transcript_seq = seq
    table.white.last_prompt_tokens = measured
    await db.commit()


async def _compacted(db: AsyncSession, table: Table) -> list[GameEvent]:
    return list(
        await db.scalars(
            sa.select(GameEvent)
            .where(GameEvent.game_id == table.game.id, GameEvent.type == EventType.COMPACTED)
            .order_by(GameEvent.seq)
        )
    )


# ====================================================================== rung one


async def test_stale_tool_results_are_elided_without_a_provider_call(
    db: AsyncSession, table: Table
) -> None:
    """The cheap rung. No summary is asked for, because there is nothing older to fold."""
    slug = "scripted/trims"
    await _register(db, slug=slug, context=60_000)
    await _history(db, table, turns=3, measured=55_000)

    result = await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"))),
        model=slug,
        limits=TurnLimits(context_reserve_tokens=50_000, keep_turns=8, max_kept_messages=100),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.llm_calls == 1, "trimming needs no provider at all"

    events = await _compacted(db, table)
    assert len(events) == 1
    assert events[0].payload["trimmed"] == 3, (
        "all three seeded turns — the newest turn is the live one, which has no result yet"
    )
    assert events[0].payload["folded"] == 0
    assert events[0].payload["characters_after"] < events[0].payload["characters_before"]


async def test_a_trimmed_result_keeps_its_place_and_its_tool_call_id(
    db: AsyncSession, table: Table
) -> None:
    """It cannot simply stop being sent: the assistant message that requested it would carry a
    `tool_call_id` with no answer, and every provider refuses that."""
    slug = "scripted/trims-two"
    await _register(db, slug=slug, context=60_000)
    await _history(db, table, turns=3, measured=55_000)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"))),
        model=slug,
        limits=TurnLimits(context_reserve_tokens=50_000, keep_turns=8, max_kept_messages=100),
    )

    sent = await transcript.build_messages(db, table.white.id)
    tools = [m for m in sent if m.get("role") == "tool"]
    assert len(tools) >= 2
    assert all(m["tool_call_id"] for m in tools), "nothing was orphaned"
    assert any(m["content"] == compaction.TRIMMED_PLACEHOLDER for m in tools)


async def test_the_record_still_holds_what_the_tool_returned(
    db: AsyncSession, table: Table
) -> None:
    """Invariant 3. `trimmed_at` records the decision; `content` is never rewritten."""
    slug = "scripted/trims-three"
    await _register(db, slug=slug, context=60_000)
    await _history(db, table, turns=3, measured=55_000)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"))),
        model=slug,
        limits=TurnLimits(context_reserve_tokens=50_000, keep_turns=8, max_kept_messages=100),
    )

    trimmed = list(
        await db.scalars(
            sa.select(TranscriptMessage).where(TranscriptMessage.trimmed_at.is_not(None))
        )
    )
    assert trimmed, "something was trimmed"
    assert all("legal moves:" in (r.content or "") for r in trimmed)


# ====================================================================== convergence


async def test_a_pass_that_would_change_nothing_is_not_called_a_success(
    db: AsyncSession, table: Table
) -> None:
    """`Plan.worthwhile` asked `bool(self.fold)`, so folding 3 messages of 44 counted as room made.

    Here the one older turn has already been trimmed and everything fits inside `keep_turns`, so
    both rungs have run out. The pass must report that rather than writing an event claiming it
    helped — which is what let one game "compact" five times without ever making room.
    """
    slug = "scripted/nothing-to-do"
    await _register(db, slug=slug, context=60_000)
    await _history(db, table, turns=1, measured=59_000)
    await db.execute(
        sa.update(TranscriptMessage)
        .where(TranscriptMessage.role == "tool")
        .values(trimmed_at=sa.func.now())
    )
    await db.commit()

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"))),
        model=slug,
        limits=TurnLimits(context_reserve_tokens=50_000, keep_turns=8, max_kept_messages=100),
    )

    assert await _compacted(db, table) == [], "no event, because nothing changed"


async def test_the_kept_turns_cannot_be_larger_than_the_window_they_fit_in(
    db: AsyncSession, table: Table
) -> None:
    """The shape that would not converge: `keep_turns=4` retaining 41 and then 50 messages."""
    slug = "scripted/converges"
    await _register(db, slug=slug, context=60_000)
    await _history(db, table, turns=10, measured=55_000)

    await play_turn(
        db,
        table,
        scripted(
            prose("A quiet Sicilian. I am a pawn up and my king is castled."),
            step(tool_call("make_move", move="e4")),
        ),
        model=slug,
        limits=TurnLimits(context_reserve_tokens=50_000, keep_turns=4, max_kept_messages=12),
    )

    events = await _compacted(db, table)
    assert len(events) == 1
    kept = events[0].payload["kept"]
    assert kept <= 13, f"the system prompt plus at most 12 messages of turns, got {kept}"
    assert events[0].payload["folded"] > 0
    assert events[0].payload["characters_after"] < events[0].payload["characters_before"] / 2


# ====================================================================== the reactive rung


async def test_a_context_length_refusal_compacts_and_retries(
    db: AsyncSession, table: Table
) -> None:
    """The refusal carries exact numbers — better than anything we computed — so it is the trigger.

    Before this, the same 400 was classified as `request_rejected` and abandoned the game outright.
    """
    slug = "scripted/reactive"
    await _register(db, slug=slug, context=256_000)
    await _history(db, table, turns=6)

    model = Refuses(
        prose("Folded. I am a pawn up in a Scotch."),
        step(tool_call("make_move", move="e4")),
    )

    result = await play_turn(
        db, table, model, model=slug, limits=TurnLimits(keep_turns=2, max_kept_messages=12)
    )

    assert result.status is TurnStatus.COMPLETED, "the game was abandoned on this before"
    assert result.move is not None and result.move.move.san == "e4"
    assert model.calls == 3, "refused, summarised, then played"

    events = await _compacted(db, table)
    assert len(events) == 1
    assert events[0].payload["occupied_tokens"] == 261751 + 502 + 1, (
        "the provider's own count of the request that failed, not ours"
    )
    assert events[0].payload["context_tokens"] == 256_000


async def test_a_second_refusal_is_not_retried_again(db: AsyncSession, table: Table) -> None:
    """Five identical rejections at ply 10 is what the first version of this cost."""
    slug = "scripted/reactive-twice"
    await _register(db, slug=slug, context=256_000)
    await _history(db, table, turns=6)

    calls = 0

    async def always_refuses(**_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise _BadRequestError(CONTEXT_400)

    result = await play_turn(db, table, always_refuses, model=slug, limits=TurnLimits(keep_turns=2))

    assert result.status is TurnStatus.FAILED
    assert result.request_rejected, "it keeps its classification once the rung is spent"
    assert calls <= 3, f"refused, summary attempt, one retry — got {calls}"

"""A rescue that outlives the turn that needed it (ADR-0032).

A turn is one transaction (ADR-0007), which is what makes a crash mid-turn safe — and what makes a
compaction *inside* a failing turn worthless. `29e7f004` and `e601f9af` were reopened three times
each and died within a second of every attempt: whatever the reactive rung managed to fold was
rolled back with the turn, so the next attempt loaded a byte-identical transcript and was refused
in exactly the same words. The 400 reported the same 227,440 tokens of text input every time,
across three resumes and five days.

So the last rescue runs in the worker, outside the turn, in a session of its own. It only elides
stale tool output — that rung needs no provider, which matters because this path runs precisely
because the provider just refused us.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import compaction
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import GameEvent, TranscriptMessage
from chessmark.db.models import Turn as TurnRow
from chessmark.game import Termination
from chessmark.orchestration.worker import ABORTED, TURN_FAILED
from tests.support import Fixture

pytestmark = pytest.mark.integration

#: The refusal, verbatim apart from the wrapping — including the breakdown the fix reads.
CONTEXT_400 = (
    "litellm.BadRequestError: OpenrouterException - "
    '{"error":{"message":"This endpoint\'s maximum context length is 256000 tokens. '
    "However, you requested about 291942 tokens (227440 of text input, 502 of tool input, "
    '64000 in the output).","code":400,"metadata":{"provider_name":null}}}'
)


class _BadRequestError(Exception):
    status_code = 400


async def refuses_for_size(**_: Any) -> dict[str, Any]:
    raise _BadRequestError(CONTEXT_400)


async def refuses_for_something_else(**_: Any) -> dict[str, Any]:
    raise _BadRequestError("litellm.BadRequestError: model does not support tools")


async def _fat_transcript(db: AsyncSession, game: Any, player: Any, *, turns: int) -> None:
    """Complete turns, each carrying a fat `get_legal_moves` result — a chess transcript's shape,
    and the bulk of what a trim can free."""
    # The seat already has its system prompt from `create_match`, so this appends after it rather
    # than writing a second row 1.
    seq = int(player.transcript_seq or 0)
    for index in range(turns):
        row = TurnRow(game_id=game.id, player_id=player.id)
        db.add(row)
        await db.flush()
        for role in ("user", "assistant", "tool", "assistant"):
            seq += 1
            db.add(
                TranscriptMessage(
                    game_id=game.id,
                    player_id=player.id,
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
    player.transcript_seq = seq
    await db.commit()


async def _trimmed(db: AsyncSession, player_id: Any) -> int:
    return len(
        list(
            await db.scalars(
                sa.select(TranscriptMessage).where(
                    TranscriptMessage.player_id == player_id,
                    TranscriptMessage.trimmed_at.is_not(None),
                )
            )
        )
    )


async def test_the_trim_survives_the_failed_turn(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """**The fix.** The turn rolls back whole; the rescue must not.

    Without it the transcript is byte-identical on the next attempt and the same refusal repeats
    until the job runs out of attempts — which is what left two games unrecoverable through three
    resumes each.
    """
    await _fat_transcript(db, game.game, game.white, turns=6)
    worker = make_worker(refuses_for_size)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == TURN_FAILED, "the job is retried rather than the game abandoned"
    db.expunge_all()
    assert await _trimmed(db, game.white.id) > 0, (
        "the trim was rolled back with the turn, so the next attempt sends the same bytes"
    )


async def test_it_is_recorded_as_a_state_change(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """Invariant 7: the log is the authority on what happened, and this changed what we replay.
    Marked so a reader can tell it from a fold the turn loop did itself."""
    await _fat_transcript(db, game.game, game.white, turns=6)
    worker = make_worker(refuses_for_size)

    await worker.handle(game.first_job)

    db.expunge_all()
    events = list(
        await db.scalars(
            sa.select(GameEvent).where(
                GameEvent.game_id == game.game.id, GameEvent.type == EventType.COMPACTED
            )
        )
    )
    assert len(events) == 1
    assert events[0].payload["recovered_outside_turn"] is True
    assert events[0].payload["trimmed"] > 0
    assert events[0].payload["folded"] == 0, "folding needs the provider that just refused us"


async def test_the_record_still_holds_what_the_tool_returned(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """Invariant 3. `trimmed_at` records the decision; `content` is never rewritten."""
    await _fat_transcript(db, game.game, game.white, turns=6)
    worker = make_worker(refuses_for_size)

    await worker.handle(game.first_job)

    db.expunge_all()
    rows = list(
        await db.scalars(
            sa.select(TranscriptMessage).where(
                TranscriptMessage.player_id == game.white.id,
                TranscriptMessage.trimmed_at.is_not(None),
            )
        )
    )
    assert rows and all("legal moves:" in (row.content or "") for row in rows)


async def test_a_transcript_with_nothing_to_free_is_abandoned(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """The honest end. A game whose transcript cannot be shrunk without the provider is not going
    to recover by being retried, and pretending otherwise would spin the job budget."""
    worker = make_worker(refuses_for_size)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == ABORTED
    db.expunge_all()
    final = await db.get(type(game.game), game.game.id)
    assert final is not None and final.status is GameStatus.ABORTED
    assert final.termination is Termination.ABANDONED


async def test_a_rejection_that_is_not_about_size_is_not_trimmed(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """Narrow on purpose. A 400 about tool support is not fixed by a smaller transcript, and
    trimming it would destroy context to no end."""
    await _fat_transcript(db, game.game, game.white, turns=6)
    worker = make_worker(refuses_for_something_else)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == ABORTED
    db.expunge_all()
    assert await _trimmed(db, game.white.id) == 0


async def test_the_placeholder_is_what_gets_sent(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """And the messages keep their place: a `tool` result that simply vanished would orphan the
    `tool_calls` that asked for it, which every provider refuses."""
    from chessmark.agents import transcript

    await _fat_transcript(db, game.game, game.white, turns=6)
    worker = make_worker(refuses_for_size)

    await worker.handle(game.first_job)

    db.expunge_all()
    sent = await transcript.build_messages(db, game.white.id)
    tools = [m for m in sent if m.get("role") == "tool"]
    assert tools and all(m["tool_call_id"] for m in tools)
    assert any(m["content"] == compaction.TRIMMED_PLACEHOLDER for m in tools)

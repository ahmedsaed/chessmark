"""A turn may not inflate its own context (ADR-0031).

The failure this file exists to prevent, measured from a real game — turn 1041 of `29e7f004`, ten
calls, one turn:

    stop         466 →     731 prompt tokens
    length    32,768 → 416,942
    tool_calls   629 → 449,772
    length    32,768 → 450,558
    ...
    length    32,768 → 516,877

Compaction ran at the top of it and worked: the prompt starts at 731 tokens. The same turn then put
half a million back, because every reply cut off at the endpoint's ceiling was appended in full and
re-sent with the next request. The model was operating its tools correctly in between — four of the
ten calls carry `tool_calls` — it simply never got to finish a thought, and each failed attempt made
the next one harder.

The rule is deliberately narrow: a truncated reply that reached **no tool call** is elided. One
truncated *after* it managed a call is kept whole, because that one has a call to justify and a
provider may require its reasoning alongside it (`test_reasoning_replay.py`).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import transcript
from chessmark.agents.compaction import TRUNCATED_PLACEHOLDER
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.turn import TurnLimits
from chessmark.db.models import TranscriptMessage
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration

#: A fragment big enough that keeping it is visible in the prompt, standing in for the 32,768-token
#: dumps the real endpoint produced.
FRAGMENT = "thinking " * 2_000


def cut_off(content: str = FRAGMENT) -> dict[str, object]:
    """A reply the endpoint stopped mid-thought, carrying no tool call."""
    return step(content=content, finish_reason="length", completion_tokens=900)


async def _rows(db: AsyncSession, player_id: object) -> list[TranscriptMessage]:
    return list(
        await db.scalars(
            sa.select(TranscriptMessage)
            .where(TranscriptMessage.player_id == player_id)
            .order_by(TranscriptMessage.seq)
        )
    )


async def test_a_truncated_fragment_is_not_replayed(db: AsyncSession, table: Table) -> None:
    """The fragment must not reach the next request. This is the whole fix.

    Without it the turn re-sends everything it was cut off saying, so the prompt grows by the
    endpoint's entire output ceiling on every retry.
    """
    await play_turn(
        db,
        table,
        scripted(cut_off(), step(tool_call("make_move", move="e4"))),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    sent = await transcript.build_messages(db, table.white.id)
    body = "".join(str(message.get("content") or "") for message in sent)

    assert FRAGMENT not in body, "the unfinished reply was replayed back to the model"
    assert TRUNCATED_PLACEHOLDER in body, "and the model was not told why it is missing"


async def test_the_row_still_holds_what_the_model_said(db: AsyncSession, table: Table) -> None:
    """Invariant 3: the record is verbatim. Only the *request* shrinks.

    A fix that deleted the text would be a fix that edited the record, and the raw payload behind
    every ply has to stay one click away.
    """
    await play_turn(
        db,
        table,
        scripted(cut_off(), step(tool_call("make_move", move="e4"))),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    marked = [row for row in await _rows(db, table.white.id) if row.truncated_at is not None]

    assert len(marked) == 1, "exactly the cut-off reply should be marked"
    assert marked[0].content == FRAGMENT, "the stored row was edited rather than merely elided"


async def test_the_prompt_does_not_grow_with_each_retry(db: AsyncSession, table: Table) -> None:
    """Three truncations in a row cost one placeholder each, not three fragments.

    The measured shape of the real failure: 731 tokens to 516,877 inside one turn.
    """
    await play_turn(
        db,
        table,
        scripted(
            cut_off(),
            cut_off(),
            cut_off(),
            step(tool_call("make_move", move="e4")),
        ),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    sent = await transcript.build_messages(db, table.white.id)
    body = "".join(str(message.get("content") or "") for message in sent)

    assert body.count(FRAGMENT) == 0
    assert body.count(TRUNCATED_PLACEHOLDER) == 3


async def test_a_truncated_reply_that_acted_is_kept_whole(db: AsyncSession, table: Table) -> None:
    """**The narrowness is the point.**

    A reply cut off *after* it emitted a tool call has a call to justify, and several providers
    require the reasoning that came with it — Gemini 3 refuses a function call whose
    `thought_signature` is missing. Eliding that one would trade this bug for the one ADR-0015
    already paid for.
    """
    await play_turn(
        db,
        table,
        scripted(
            step(
                tool_call("make_move", move="e4"),
                content=FRAGMENT,
                finish_reason="length",
                completion_tokens=900,
            )
        ),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    rows = await _rows(db, table.white.id)

    assert all(row.truncated_at is None for row in rows), "a reply that acted was elided"
    sent = await transcript.build_messages(db, table.white.id)
    body = "".join(str(message.get("content") or "") for message in sent)
    assert FRAGMENT in body, "the reasoning behind a tool call must still be replayed"


async def test_an_ordinary_reply_is_untouched(db: AsyncSession, table: Table) -> None:
    """A model that talks before acting is not truncated and must not be elided (AGENT-05)."""
    await play_turn(
        db,
        table,
        scripted(
            step(content="Let me look at the board first."),
            step(tool_call("make_move", move="e4")),
        ),
    )

    rows = await _rows(db, table.white.id)
    assert all(row.truncated_at is None for row in rows)

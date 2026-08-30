"""The agent's conversation, stored append-only and rebuilt each turn.

An agent sees the **entire game** every turn — that is a product requirement, not an optimisation
(ADR-0003). Naively that costs O(n²) prompt tokens over a game, which prompt caching removes, but
only if the replayed prefix is byte-identical between calls.

Rather than ask the turn loop to be careful about that, the guarantee is structural:

* Messages are **rows**. Appending is an INSERT; there is no code path that rewrites one.
* The transcript is rebuilt by `SELECT ... ORDER BY seq`, so turn N+1's list is turn N's list plus
  whatever was appended in between. Byte-for-byte, by construction.
* The system prompt is row 1 and is written once, at game start, from a versioned template.

`tests/agents/test_transcript.py` asserts the prefix-extension property directly, because it is
the single thing that keeps a 60-move game affordable.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.compaction import TRIMMED_PLACEHOLDER, live_messages
from chessmark.db.models import Player, TranscriptMessage


class TranscriptError(RuntimeError):
    pass


async def append_message(
    session: AsyncSession,
    *,
    player_id: uuid.UUID,
    game_id: uuid.UUID,
    role: str,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
    reasoning_details: list[dict[str, Any]] | None = None,
    turn_id: int | None = None,
    is_summary: bool = False,
) -> TranscriptMessage:
    """Append one message.

    `seq` is allocated from `players.transcript_seq` under a row lock, the same mechanism that
    keeps `game_events` gap-free (ADR-0008).
    """
    seq = await session.scalar(
        sa.update(Player)
        .where(Player.id == player_id)
        .values(transcript_seq=Player.transcript_seq + 1)
        .returning(Player.transcript_seq)
    )
    if seq is None:
        msg = f"no player with id {player_id}"
        raise TranscriptError(msg)

    message = TranscriptMessage(
        game_id=game_id,
        player_id=player_id,
        turn_id=turn_id,
        seq=seq,
        role=role,
        is_summary=is_summary,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        name=name,
        reasoning_details=reasoning_details,
    )
    session.add(message)
    await session.flush()
    return message


async def append_messages(
    session: AsyncSession,
    *,
    player_id: uuid.UUID,
    game_id: uuid.UUID,
    messages: list[dict[str, Any]],
    turn_id: int | None = None,
) -> list[TranscriptMessage]:
    """Append several messages in order."""
    return [
        await append_message(
            session,
            player_id=player_id,
            game_id=game_id,
            turn_id=turn_id,
            role=str(message["role"]),
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            tool_call_id=message.get("tool_call_id"),
            name=message.get("name"),
            reasoning_details=message.get("reasoning_details"),
        )
        for message in messages
    ]


def to_provider_message(row: TranscriptMessage) -> dict[str, Any]:
    """Render one stored row into the shape the provider expects.

    Keys are emitted in a fixed order and null fields are omitted entirely, so the same row always
    serialises identically. Anything less would defeat the caching this whole module exists for.
    """
    message: dict[str, Any] = {"role": row.role}

    if row.role == "tool":
        message["tool_call_id"] = row.tool_call_id or ""
        if row.name:
            message["name"] = row.name
        # A trimmed result keeps its place and its `tool_call_id` — the assistant message that
        # requested it still has an answer — and carries a placeholder instead of what the tool
        # returned (ADR-0021). The row is untouched; only the request shrinks.
        message["content"] = TRIMMED_PLACEHOLDER if row.trimmed_at else (row.content or "")
        return message

    if row.content is not None:
        message["content"] = row.content
    if row.tool_calls:
        message["tool_calls"] = row.tool_calls
        message.setdefault("content", None)

    # Echoed back untouched. Several models treat their own prior reasoning as part of the history
    # they require: Gemini 3 rejects a function call missing its `thought_signature`, DeepSeek
    # rejects a thinking-mode history missing `reasoning_content`, and OpenRouter requires the
    # sequence be replayed exactly as it arrived. Dropping it is what made `deepseek-v4-pro` emit
    # raw DSML markup instead of tool calls and forfeit a game.
    if row.reasoning_details:
        message["reasoning_details"] = row.reasoning_details

    return message


async def build_messages(session: AsyncSession, player_id: uuid.UUID) -> list[dict[str, Any]]:
    """The message list to send this turn.

    The whole transcript, every turn — **except what a compaction has folded** (ADR-0018). Folded
    rows stay in the table and stop being sent, so the record is still verbatim and the request is
    smaller. An uncompacted game reads exactly as it always did: nothing is superseded, so this is
    every row in `seq` order.
    """
    rows = await live_messages(session, player_id)
    return [to_provider_message(row) for row in rows]


async def full_history(session: AsyncSession, player_id: uuid.UUID) -> list[dict[str, Any]]:
    """Everything ever sent, folded rows included — the record rather than the request.

    Nothing on the playing path uses this. It exists because "we keep the whole history" should be
    demonstrable rather than merely asserted, and a test asserts the two differ only by what a
    compaction folded.
    """
    rows = await session.scalars(
        sa.select(TranscriptMessage)
        .where(TranscriptMessage.player_id == player_id)
        .order_by(TranscriptMessage.seq)
    )
    return [to_provider_message(row) for row in rows]


async def transcript_length(session: AsyncSession, player_id: uuid.UUID) -> int:
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(TranscriptMessage)
        .where(TranscriptMessage.player_id == player_id)
    )
    return int(count or 0)


def assistant_message(
    *,
    content: str | None,
    tool_calls: list[dict[str, Any]] | None,
    reasoning_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the assistant turn to append after a provider response."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
        "reasoning_details": reasoning_details,
    }


def tool_result_message(*, tool_call_id: str, name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}

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
    turn_id: int | None = None,
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
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        name=name,
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
        message["content"] = row.content or ""
        return message

    if row.content is not None:
        message["content"] = row.content
    if row.tool_calls:
        message["tool_calls"] = row.tool_calls
        message.setdefault("content", None)

    return message


async def build_messages(session: AsyncSession, player_id: uuid.UUID) -> list[dict[str, Any]]:
    """The full message list to send this turn.

    This is the whole transcript, every turn. See the module docstring for why that is affordable.
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
    *, content: str | None, tool_calls: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Build the assistant turn to append after a provider response."""
    return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def tool_result_message(*, tool_call_id: str, name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}

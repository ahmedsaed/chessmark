"""Withholding a live opponent's thinking from the person playing against it.

Invariant 8 and HUMAN-07: reasoning is never exposed **mid-game**, because it would hand a model's
plan to whoever it is playing. The same goes for the prose a model writes alongside its moves —
Gemini says everything there and nothing in `reasoning`, so publishing one and not the other would
leak exactly what the gate exists to hold back.

**Who is at risk decides the rule.** Two models cannot read this stream — each sees only its own
transcript — so showing an audience both sides thinking leaks nothing and is the whole appeal
(ADR-0013). A human, however, is sitting on the page reading it.

This used to be enforced when the event was *written* (`agents/turn.py` simply left the text out of
the payload for any game with a human seat). That was airtight and permanent: `game_events` is
append-only (ADR-0008), so what was never written could never be revealed, and a person's own games
— the ones they would most want to read back — were the only games whose reasoning the transcript
could never show. The text now goes into the log always and is withheld **here**, on the way out,
which restores "hidden during, readable after" without weakening anything mid-game.

Every path that serves `game_events` to a browser must apply this. There are two — the REST log and
the SSE stream — and missing either would leak a live opponent's plan to the person playing it.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import Game, Player

#: The key each event type carries that a live opponent must not read.
WITHHELD_KEYS: dict[str, str] = {
    str(EventType.THINKING): "reasoning",
    str(EventType.OUTPUT): "content",
}

#: A game in one of these is over, and nothing needs holding back any more.
_CONCLUDED = {GameStatus.FINISHED, GameStatus.ABORTED}


async def has_human_player(session: AsyncSession, game_id: uuid.UUID) -> bool:
    """Whether a person holds a seat, and so is reading the stream as a participant."""
    seat = await session.scalar(
        sa.select(Player.id)
        .where(Player.game_id == game_id, Player.kind == PlayerKind.HUMAN)
        .limit(1)
    )
    return seat is not None


async def must_withhold_thinking(session: AsyncSession, game: Game) -> bool:
    """Whether this game's outgoing events must have their reasoning and prose stripped.

    Only while the game is live **and** a person is playing it. A finished game reveals everything
    (HUMAN-07 is about mid-game), and a game between two models has no participant reading the
    page at all.
    """
    if game.status in _CONCLUDED:
        return False
    return await has_human_player(session, game.id)


def redact(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One event's payload with the withheld key removed.

    The token count deliberately stays: "it is thinking, and here is how much" is not a leak, and
    it is what the live view shows while a turn is in flight. The shape is otherwise exactly what
    the write-time redaction used to produce, so nothing downstream needs to know this changed.
    """
    key = WITHHELD_KEYS.get(event_type)
    if key is None or key not in payload:
        return payload
    return {name: value for name, value in payload.items() if name != key}

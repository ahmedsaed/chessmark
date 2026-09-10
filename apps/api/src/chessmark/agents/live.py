"""Live frames: what a turn is doing, before it is a fact (ADR-0035).

A turn is one transaction (ADR-0007), so nothing it appends can be published until it commits —
and a turn takes as long as the model does. Ply 8 of `e601f9af` ran **632 seconds** across six
provider rounds and delivered all fifteen of its events in one frame at the end, every one stamped
the same millisecond. A spectator watched a still board for ten minutes.

What travels here is deliberately *not* an event:

* it has no `seq` and is never written down, so invariant 7 still reads "one `game_events` row per
  state change";
* it is a prediction that the turn will commit, which is usually right and occasionally wrong —
  a failing round rolls the turn back and the frames it already sent describe something that, in
  the record, never happened;
* the committed events supersede it moments later, carrying the same content with sequence
  numbers, which is what makes a reconnect identical to today's.

That is the whole bargain: a spectator sees the turn assemble, and the record is untouched.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any, Protocol

#: Separate from the event channel so a subscriber can take one without the other, and so a delta
#: can never be mistaken for a committed event by a client reading either.
DELTA_CHANNEL = "chessmark:live:{game_id}"


class LiveChannel(Protocol):
    """Somewhere to send a frame. Redis in production; a list in tests."""

    async def send(self, game_id: uuid.UUID, frame: dict[str, Any]) -> None: ...


class RedisLive:
    """Publishes frames to Redis, and never raises.

    Best-effort in the strongest sense: a frame is not a record, so failing to send one costs a
    spectator a few seconds of animation and costs the game nothing. Failing a turn over it would
    trade the thing that matters for the thing that does not.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def send(self, game_id: uuid.UUID, frame: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await self._redis.publish(DELTA_CHANNEL.format(game_id=game_id), json.dumps(frame))


class NullLive:
    """Sends nothing. The default, so every path that has no channel simply does not stream."""

    async def send(self, game_id: uuid.UUID, frame: dict[str, Any]) -> None:
        return None


def turn_started(player_id: uuid.UUID, *, colour: str, ply: int, model: str) -> dict[str, Any]:
    """A turn has begun, said before its transaction can say it.

    Without this a spectator receives blocks for a turn nothing has announced: `turn_started` is
    appended *inside* the turn's transaction like everything else, so it does not reach anyone
    until the turn is over — which is the moment the blocks stop being needed. The panel opens a
    provisional turn on this and hangs the rounds off it.

    Same fields as the event that supersedes it, so the client folds one shape.
    """
    return {
        "frame": "turn",
        "player_id": str(player_id),
        "colour": colour,
        "ply": ply,
        "model": model,
    }


def block(player_id: uuid.UUID, kind: str, **fields: Any) -> dict[str, Any]:
    """One finished step of a turn — a whole reasoning block, a whole tool call.

    Shaped like the payload of the event that will carry the same content once the turn commits, so
    the client folds a frame and an event through the same code and cannot render them differently.
    """
    return {"frame": "block", "player_id": str(player_id), "kind": kind, **fields}


def token(player_id: uuid.UUID, kind: str, text: str) -> dict[str, Any]:
    """A fragment of a block still being generated.

    `kind` is `reasoning` or `output`, matching the two channels a provider streams separately. The
    client appends these to a provisional block and replaces the whole thing when the block frame
    arrives, so a dropped fragment costs a flicker rather than a wrong transcript — nothing here is
    ever the source of the text that gets stored.
    """
    return {"frame": "token", "player_id": str(player_id), "kind": kind, "text": text}


__all__ = [
    "DELTA_CHANNEL",
    "LiveChannel",
    "NullLive",
    "RedisLive",
    "block",
    "token",
    "turn_started",
]

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

#: The frames of the turn currently in flight, kept so somebody arriving mid-turn can be caught up.
#:
#: **Pub/sub is fire-and-forget, and a turn is long.** A spectator who opens a game while a model
#: is nine minutes into a round would otherwise receive the committed backfill — everything up to
#: the *last* turn — and then sit in front of a still board until this one commits, which is
#: exactly the experience ADR-0035 set out to end. They would be the only reader not getting it.
BUFFER_KEY = "chessmark:live:{game_id}:turn"

#: How long that buffer outlives the turn it belongs to. Generous, because a turn can be: the
#: longest observed round alone was 369 seconds and a whole turn 632. It is a fallback — a new
#: turn clears the buffer outright — so the only thing this bounds is how long a rolled-back
#: turn's frames linger in Redis before they expire on their own.
BUFFER_TTL_SECONDS = 3600

#: A hard ceiling on the buffer, so a model that emits fifty thousand fragments cannot grow it
#: without bound. Reached only by a pathological turn; past it a late joiner sees the tail, which
#: is the part that is still on screen anyway.
BUFFER_MAX_FRAMES = 400


class LiveChannel(Protocol):
    """Somewhere to send a frame. Redis in production; a list in tests."""

    async def send(self, game_id: uuid.UUID, frame: dict[str, Any]) -> None: ...

    async def replay(self, game_id: uuid.UUID) -> list[dict[str, Any]]: ...


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
            body = json.dumps(frame)
            buffer = BUFFER_KEY.format(game_id=game_id)

            pipe = self._redis.pipeline()
            # A new turn supersedes the last one's frames wholesale — including a rolled-back
            # turn's, which is the only thing that ever leaves stale ones behind.
            if frame.get("frame") == "turn":
                pipe.delete(buffer)
            pipe.rpush(buffer, body)
            pipe.ltrim(buffer, -BUFFER_MAX_FRAMES, -1)
            pipe.expire(buffer, BUFFER_TTL_SECONDS)
            pipe.publish(DELTA_CHANNEL.format(game_id=game_id), body)
            await pipe.execute()

    async def replay(self, game_id: uuid.UUID) -> list[dict[str, Any]]:
        """The in-flight turn's frames, for a reader who has just arrived.

        Empty when no turn is running, when the last one committed, or when Redis is unreachable —
        all of which are the same thing to a caller: there is nothing to catch up on.
        """
        with contextlib.suppress(Exception):
            raw = await self._redis.lrange(BUFFER_KEY.format(game_id=game_id), 0, -1)
            return [json.loads(item) for item in raw]
        return []


class NullLive:
    """Sends nothing. The default, so every path that has no channel simply does not stream."""

    async def send(self, game_id: uuid.UUID, frame: dict[str, Any]) -> None:
        return None

    async def replay(self, game_id: uuid.UUID) -> list[dict[str, Any]]:
        return []


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
    "BUFFER_KEY",
    "BUFFER_MAX_FRAMES",
    "BUFFER_TTL_SECONDS",
    "DELTA_CHANNEL",
    "LiveChannel",
    "NullLive",
    "RedisLive",
    "block",
    "token",
    "turn_started",
]

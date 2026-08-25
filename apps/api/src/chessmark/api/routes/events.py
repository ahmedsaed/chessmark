"""The live event stream (ADR-0004, ADR-0008).

Server-Sent Events over a plain GET. The traffic is one-directional — the server pushes, the
client watches — so WebSockets would buy nothing and cost a second protocol, manual heartbeats,
and hand-rolled reconnection.

**The ordering here is the whole trick.** Subscribing must happen *before* reading the backfill:

    1. subscribe to the Redis channel     ← live events start buffering
    2. read missed events from Postgres
    3. emit the backfill
    4. emit live events, skipping any at or below the last backfilled seq

Do it the other way round and an event committed between the read and the subscribe is lost
forever — the client waits for a sequence number that never arrives. Subscribing first can only
produce *duplicates*, which step 4 removes; reading first produces *gaps*, which nothing can.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from chessmark.api.deps import GameDep, RedisDep, SessionDep
from chessmark.api.redaction import must_withhold_thinking, redact
from chessmark.api.schemas import EventOut
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game
from chessmark.db.repositories import load_events
from chessmark.orchestration.worker import EVENT_CHANNEL

log = logging.getLogger(__name__)

router = APIRouter(prefix="/games", tags=["events"])

#: Comment frames keep proxies from timing the connection out on a quiet game — a model can
#: legitimately think for minutes.
HEARTBEAT_SECONDS = 15.0


def _resolve_cursor(last_event_id: str | None, after_seq: int) -> int:
    """`Last-Event-ID` wins over the query parameter — the browser sends it automatically on
    reconnect, and it is the more recent of the two by definition."""
    if last_event_id:
        try:
            return max(int(last_event_id), 0)
        except ValueError:
            log.warning("ignoring unparseable Last-Event-ID %r", last_event_id)
    return after_seq


def _frame(event: EventOut) -> dict[str, Any]:
    """One SSE frame. `id` is what the browser echoes back as `Last-Event-ID`."""
    return {
        "id": str(event.seq),
        "event": str(event.type),
        "data": json.dumps(
            {
                "seq": event.seq,
                "type": str(event.type),
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
        ),
    }


@router.get("/{game_id}/stream")
async def stream_events(
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    game: GameDep,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    """Stream a game's events, resuming from a cursor if one is given (UI-10)."""
    cursor = _resolve_cursor(last_event_id, after_seq)
    channel = EVENT_CHANNEL.format(game_id=game.id)

    # Decided once, before anything is sent. A person reading their own live game must not be
    # handed their opponent's reasoning or prose (invariant 8, HUMAN-07) — and this stream is the
    # path that would deliver it to them fastest. See `api/redaction.py`.
    #
    # If the game ends mid-stream the answer stays "withhold" for the rest of this connection.
    # That is deliberate: the client is told the game ended and re-reads the finished log, which
    # comes back complete. Re-evaluating per event would mean querying the seats on every frame.
    withhold = await must_withhold_thinking(session, game)

    def visible(event: EventOut) -> EventOut:
        if not withhold:
            return event
        return event.model_copy(update={"payload": redact(str(event.type), event.payload)})

    async def publisher() -> AsyncIterator[dict[str, Any]]:
        pubsub = redis.pubsub()
        # Step 1: subscribe first. Anything committed from here on is buffered for us.
        await pubsub.subscribe(channel)
        delivered = cursor

        try:
            # Step 2 and 3: everything the client missed, straight from the durable log.
            backfill = await load_events(session, game.id, after_seq=cursor)
            for row in backfill:
                yield _frame(visible(EventOut.from_model(row)))
                delivered = row.seq

            if _already_over(game, backfill):
                return

            # Step 4: live. Anything at or below `delivered` was already sent in the backfill.
            last_beat = asyncio.get_event_loop().time()
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                now = asyncio.get_event_loop().time()

                if message is None:
                    if now - last_beat >= HEARTBEAT_SECONDS:
                        last_beat = now
                        yield {"event": "heartbeat", "data": "{}"}
                    continue

                parsed = _parse(message.get("data"))
                if parsed is None or parsed["seq"] <= delivered:
                    continue

                delivered = parsed["seq"]
                last_beat = now
                # The live branch is by definition mid-game, so this is the frame that would leak.
                if withhold:
                    parsed["payload"] = redact(str(parsed["type"]), parsed.get("payload") or {})
                yield {
                    "id": str(parsed["seq"]),
                    "event": parsed["type"],
                    "data": json.dumps(parsed),
                }

                if parsed["type"] == str(EventType.GAME_ENDED):
                    return
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()  # type: ignore[attr-defined]

    return EventSourceResponse(publisher())


def _already_over(game: Game, backfill: list[Any]) -> bool:
    """Close the stream immediately for a finished game.

    Holding a connection open for a game that ended before the client connected would leave a
    replay viewer waiting on events that will never come.
    """
    if game.status in {GameStatus.FINISHED, GameStatus.ABORTED}:
        return True
    return any(str(row.type) == str(EventType.GAME_ENDED) for row in backfill)


def _parse(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if isinstance(data, bytes):
        data = data.decode()
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        log.warning("dropping unparseable pub/sub payload")
        return None
    return parsed if isinstance(parsed, dict) and "seq" in parsed else None

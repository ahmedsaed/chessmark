"""The SSE stream: backfill, reconnect, and no-gaps (UI-10, ADR-0004, ADR-0008).

The property under test is that a client which drops and reconnects sees **exactly** the events it
missed — no gap, no duplicate. A gap means a spectator waits forever for a sequence number that
never arrives; a duplicate means the board renders a move twice.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from chessmark.agents.scripted import plays, says, scripted, step, tool_call
from chessmark.db.enums import EventType
from tests.api.conftest import parse_sse
from tests.support import Fixture, both_sides, run_next

pytestmark = pytest.mark.integration


def resigns() -> Any:
    """A fresh scripted model that resigns immediately, ending the game in one turn."""
    return scripted(step(tool_call("resign")))


async def read_stream(
    client: AsyncClient, game_id: uuid.UUID, **kwargs: Any
) -> list[dict[str, str]]:
    """Read a stream to completion.

    Only safe once the game has ended — that is what closes the connection. A live game would
    block until the heartbeat timeout, which is the correct behaviour but useless in a test.
    """
    async with client.stream("GET", f"/games/{game_id}/stream", **kwargs) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in response.aiter_text()])
    return parse_sse(body)


def seqs(frames: list[dict[str, str]]) -> list[int]:
    return [int(frame["id"]) for frame in frames if "id" in frame]


async def play_to_mate(game: Fixture, make_worker: Any) -> None:
    worker = make_worker(both_sides(["f3", "g4"], ["e5", "Qh4"]))
    for _ in range(4):
        if await run_next(worker, game.queue) is None:
            break


# ====================================================================== backfill


async def test_a_finished_game_streams_its_whole_log_and_closes(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    """A replay viewer must not be left holding a connection for a game that already ended."""
    await play_to_mate(game, make_worker)

    frames = await asyncio.wait_for(read_stream(client, game.game.id), timeout=15)
    delivered = seqs(frames)

    assert delivered == sorted(delivered), "events must arrive in order"
    assert delivered == list(range(1, len(delivered) + 1)), "no gaps"
    assert frames[-1]["event"] == str(EventType.GAME_ENDED)


async def test_the_stream_carries_event_payloads(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    worker = make_worker(scripted(says("Take that.", tool_call("resign"))))
    await run_next(worker, game.queue)

    frames = await asyncio.wait_for(read_stream(client, game.game.id), timeout=15)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for frame in frames:
        if "event" in frame and "data" in frame:
            by_type.setdefault(frame["event"], []).append(json.loads(frame["data"]))

    assert by_type["message_sent"][0]["payload"]["content"] == "Take that."
    assert by_type["game_ended"][0]["payload"]["termination"] == "resignation"
    assert by_type["game_ended"][0]["payload"]["result"] == "0-1"


# ====================================================================== reconnect


async def test_a_cursor_replays_exactly_what_was_missed(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    """Exit criterion: reconnecting with Last-Event-ID delivers the gap and nothing else."""
    await play_to_mate(game, make_worker)

    everything = seqs(await asyncio.wait_for(read_stream(client, game.game.id), timeout=15))
    assert len(everything) > 4

    cut = everything[len(everything) // 2]
    missed = seqs(
        await asyncio.wait_for(
            read_stream(client, game.game.id, headers={"Last-Event-ID": str(cut)}), timeout=15
        )
    )

    assert missed == [seq for seq in everything if seq > cut]
    assert cut not in missed, "the cursor event itself must not be resent"


async def test_the_query_parameter_works_too(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    """Not every client is a browser; `after_seq` is the explicit form."""
    await run_next(make_worker(plays(["e4"])), game.queue)
    await run_next(make_worker(resigns()), game.queue)

    everything = seqs(await asyncio.wait_for(read_stream(client, game.game.id), timeout=15))
    tail = seqs(
        await asyncio.wait_for(
            read_stream(client, game.game.id, params={"after_seq": everything[0]}), timeout=15
        )
    )

    assert tail == everything[1:]


async def test_a_header_beats_the_query_parameter(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    """A browser sets Last-Event-ID automatically, and it is the more recent of the two."""
    await run_next(make_worker(resigns()), game.queue)

    everything = seqs(await asyncio.wait_for(read_stream(client, game.game.id), timeout=15))
    frames = seqs(
        await asyncio.wait_for(
            read_stream(
                client,
                game.game.id,
                params={"after_seq": 0},
                headers={"Last-Event-ID": str(everything[0])},
            ),
            timeout=15,
        )
    )

    assert frames == everything[1:]


async def test_an_unparseable_cursor_falls_back_to_the_beginning(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    """A malformed header must not cost the client its whole stream."""
    await run_next(make_worker(resigns()), game.queue)

    frames = seqs(
        await asyncio.wait_for(
            read_stream(client, game.game.id, headers={"Last-Event-ID": "banana"}), timeout=15
        )
    )

    assert frames[0] == 1


async def test_a_cursor_past_the_end_yields_nothing_new(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    await run_next(make_worker(resigns()), game.queue)

    everything = seqs(await asyncio.wait_for(read_stream(client, game.game.id), timeout=15))
    frames = seqs(
        await asyncio.wait_for(
            read_stream(client, game.game.id, params={"after_seq": everything[-1]}), timeout=15
        )
    )

    assert frames == []


# ====================================================================== live


async def test_a_connected_spectator_receives_a_turn_as_it_happens(
    client: AsyncClient, game: Fixture, make_worker: Any
) -> None:
    """The live half: subscribe first, then a turn commits, and the frames arrive."""
    received: list[int] = []

    async def watch() -> None:
        async with client.stream("GET", f"/games/{game.game.id}/stream") as response:
            async for line in response.aiter_lines():
                if line.startswith("id:"):
                    received.append(int(line.split(":", 1)[1].strip()))
                elif line.strip() == f"event: {EventType.GAME_ENDED}":
                    return

    reader = asyncio.create_task(watch())
    await asyncio.sleep(0.5)  # let the subscription attach before anything is published

    await run_next(make_worker(resigns(), publish=True), game.queue)

    try:
        await asyncio.wait_for(reader, timeout=15)
    except TimeoutError:
        reader.cancel()

    assert received, "a connected spectator received nothing"
    assert received == sorted(received)
    assert len(set(received)) == len(received), "no duplicates"


# ====================================================================== errors


async def test_a_missing_game_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/games/00000000-0000-0000-0000-000000000000/stream")
    assert response.status_code == 404

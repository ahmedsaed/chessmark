"""Playing a model over HTTP (Phase 10).

These are the exit criteria that only hold end to end: that a crafted request bypassing the client
is refused, that a reload restores the game exactly, and that the model's reasoning stays private
until the game is over.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game
from tests.api.conftest import as_user, fund
from tests.orchestration.conftest import both_sides, run_next

pytestmark = pytest.mark.integration


async def _seed_model(db: AsyncSession) -> None:
    await sync_model_registry(
        db, [{"openrouter_id": "test/opponent", "display_name": "Opponent Model"}]
    )
    await db.commit()


async def _new_game(client: AsyncClient, db: AsyncSession, *, colour: str = "white", **body):
    await _seed_model(db)
    # Sitting down costs credits now (ADR-0016); a test user holds none until granted.
    await fund(db)
    response = await client.post(
        "/games/human",
        json={"model": "test/opponent", "colour": colour, **body},
        headers=as_user(),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ====================================================================== seating


async def test_sitting_down_creates_a_running_unranked_game(
    client: AsyncClient, db: AsyncSession
) -> None:
    game_id = await _new_game(client, db)

    row = await db.scalar(sa.select(Game).where(Game.id == game_id))
    assert row.status is GameStatus.RUNNING
    # Never ranked: a person is not a contestant (BENCH-04).
    assert row.is_ranked is False

    detail = (await client.get(f"/games/{game_id}")).json()
    kinds = {p["colour"]: p["kind"] for p in detail["players"]}
    assert kinds == {"white": "human", "black": "model"}


async def test_playing_a_game_needs_an_account(client: AsyncClient, db: AsyncSession) -> None:
    await _seed_model(db)
    response = await client.post("/games/human", json={"model": "test/opponent"})
    assert response.status_code == 401


# ====================================================================== moving


async def test_a_legal_move_is_accepted_and_hands_the_model_its_turn(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    game_id = await _new_game(client, db)

    response = await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ply"] == 1
    assert body["game_over"] is False

    # The model's reply was enqueued, so a worker can pick it up.
    worker = make_worker(both_sides([], ["e5"]))
    handled = await run_next(worker, queue)
    assert handled is not None

    detail = (await client.get(f"/games/{game_id}")).json()
    assert detail["moves"] == ["e4", "e5"]


async def test_an_illegal_move_is_refused_with_the_legal_list(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A crafted request that never touched the client meets the same referee (invariant 1)."""
    game_id = await _new_game(client, db)

    response = await client.post(f"/games/{game_id}/moves", json={"move": "e5"}, headers=as_user())

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "e4" in detail["legal_moves"]

    # Refused, not forfeited: it is still their turn and the game is untouched.
    after = (await client.get(f"/games/{game_id}")).json()
    assert after["status"] == "running"
    assert after["ply_count"] == 0


async def test_nonsense_that_is_not_even_a_move_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    game_id = await _new_game(client, db)
    response = await client.post(f"/games/{game_id}/moves", json={"move": "zz9"}, headers=as_user())
    assert response.status_code == 422


async def test_a_stranger_cannot_move_in_your_game(client: AsyncClient, db: AsyncSession) -> None:
    """Holding the URL is not holding the seat."""
    game_id = await _new_game(client, db)

    response = await client.post(
        f"/games/{game_id}/moves",
        json={"move": "e4"},
        headers=as_user("user_stranger", email="stranger@chessmark.test"),
    )
    assert response.status_code == 403

    after = (await client.get(f"/games/{game_id}")).json()
    assert after["ply_count"] == 0


async def test_moving_without_a_token_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    game_id = await _new_game(client, db)
    response = await client.post(f"/games/{game_id}/moves", json={"move": "e4"})
    assert response.status_code == 401


async def test_a_resubmitted_move_conflicts_rather_than_playing_twice(
    client: AsyncClient, db: AsyncSession
) -> None:
    game_id = await _new_game(client, db)

    first = await client.post(
        f"/games/{game_id}/moves", json={"move": "e4", "expected_ply": 0}, headers=as_user()
    )
    assert first.status_code == 200

    second = await client.post(
        f"/games/{game_id}/moves", json={"move": "d4", "expected_ply": 0}, headers=as_user()
    )
    assert second.status_code == 409

    after = (await client.get(f"/games/{game_id}")).json()
    assert after["moves"] == ["e4"]


# ====================================================================== reload


async def test_reloading_restores_the_exact_position_and_history(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """HUMAN-04. Nothing is held in the browser: the game is rebuilt from Postgres."""
    game_id = await _new_game(client, db)

    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    worker = make_worker(both_sides([], ["e5"]))
    await run_next(worker, queue)
    await client.post(f"/games/{game_id}/moves", json={"move": "Nf3"}, headers=as_user())

    detail = (await client.get(f"/games/{game_id}")).json()
    assert detail["moves"] == ["e4", "e5", "Nf3"]
    assert detail["ply_count"] == 3
    assert detail["current_fen"].startswith("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R")

    events = (await client.get(f"/games/{game_id}/events")).json()
    assert [e["payload"]["san"] for e in events if e["type"] == "move_made"] == [
        "e4",
        "e5",
        "Nf3",
    ]


# ====================================================================== ending


async def test_resigning_ends_the_game(client: AsyncClient, db: AsyncSession) -> None:
    game_id = await _new_game(client, db)

    response = await client.post(f"/games/{game_id}/resign", headers=as_user())
    assert response.status_code == 200
    body = response.json()
    assert body["game_over"] is True
    assert body["result"] == "0-1"
    assert body["termination"] == "resignation"


async def test_a_human_can_win_by_checkmate(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """A full game, played to a real result — the phase's headline criterion."""
    game_id = await _new_game(client, db)
    worker = make_worker(both_sides([], ["e5", "Nc6", "Nf6"]))

    for move in ["e4", "Bc4", "Qh5"]:
        response = await client.post(
            f"/games/{game_id}/moves", json={"move": move}, headers=as_user()
        )
        assert response.status_code == 200, response.text
        await run_next(worker, queue)

    final = await client.post(f"/games/{game_id}/moves", json={"move": "Qxf7#"}, headers=as_user())

    assert final.status_code == 200, final.text
    body = final.json()
    assert body["game_over"] is True
    assert body["result"] == "1-0"
    assert body["termination"] == "checkmate"
    assert body["status"] == "finished"


async def test_a_finished_game_refuses_further_moves(client: AsyncClient, db: AsyncSession) -> None:
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/resign", headers=as_user())

    response = await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    assert response.status_code == 409


# ====================================================================== privacy


THINKING = "I will take the centre and then go after f7."
PROSE = "A classical reply."


def _thinks_then_resigns():
    """A scripted Black that reasons, narrates, and then resigns — ending the game in one turn.

    Ending it from *inside* the turn matters for the stream test: httpx's ASGI transport delivers
    a response body only once it is complete, so the frames arrive only when the stream closes,
    and it closes on `game_ended`. Ending the game with a second HTTP call instead would queue
    behind the open stream and deliver nothing at all.
    """
    from chessmark.agents.scripted import step, tool_call

    async def _complete(**kwargs):
        return step(tool_call("resign"), reasoning=THINKING, content=PROSE)

    return _complete


def _thinks_and_moves(move: str):
    """A scripted Black that both reasons and narrates, so there is something to withhold.

    The previous version of this test used a model that did neither, so "no reasoning is visible"
    held for the wrong reason and would have passed with the gate deleted.
    """
    from chessmark.agents.scripted import step, tool_call

    async def _complete(**kwargs):
        return step(tool_call("make_move", move=move), reasoning=THINKING, content=PROSE)

    return _complete


def _payload(events: list[dict], type_: str) -> dict:
    matching = [event["payload"] for event in events if event["type"] == type_]
    assert matching, f"expected a {type_} event, got {[e['type'] for e in events]}"
    return matching[0]


async def test_reasoning_is_withheld_while_a_human_game_is_live(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """HUMAN-07, and the sharpest case for invariant 8.

    In a model-vs-model game the reasoning goes out to spectators. Here the opponent *is* the
    spectator: a live reasoning trace would hand the person the model's plan.

    Withheld **on the way out** rather than never recorded, so it can be revealed when the game
    ends — see `api/redaction.py`. The event is therefore present throughout; what changes is
    whether it carries the text.
    """
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    await run_next(make_worker(_thinks_and_moves("e5")), queue)

    live = (await client.get(f"/games/{game_id}/events")).json()

    # The turn is visible and so is the token count — "it is thinking, and this much" is not a
    # leak, and the live view needs it. The text is not.
    thinking = _payload(live, "thinking")
    assert "reasoning" not in thinking
    assert "tokens" in thinking

    # Prose is the same leak by another route: Gemini says everything in `content` and nothing in
    # `reasoning`, so publishing one and withholding the other would defeat the gate entirely.
    assert "content" not in _payload(live, "output")

    turns = (await client.get(f"/games/{game_id}/turns")).json()
    assert all(not t.get("reasoning") for t in turns)

    # Once it is over, everything opens up.
    await client.post(f"/games/{game_id}/resign", headers=as_user())

    after = (await client.get(f"/games/{game_id}/events")).json()
    assert _payload(after, "thinking")["reasoning"] == THINKING
    assert _payload(after, "output")["content"] == PROSE


async def test_a_finished_human_game_keeps_its_reasoning_forever(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """The regression this whole change exists for.

    The gate used to run when the event was *written*, so a human game's reasoning was never in
    the log at all. `game_events` is append-only (ADR-0008), which made that omission permanent:
    a person's own games were the only ones whose thinking the transcript could never show, long
    after there was anything left to leak.
    """
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    await run_next(make_worker(_thinks_and_moves("e5")), queue)
    await client.post(f"/games/{game_id}/resign", headers=as_user())

    events = (await client.get(f"/games/{game_id}/events")).json()

    assert _payload(events, "thinking")["reasoning"] == THINKING


async def test_the_live_stream_withholds_it_too(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """The second read path, and the one that would leak fastest.

    The REST log and the SSE stream serve the same rows (ADR-0008). Gating only one of them would
    withhold the reasoning from a reload and hand it to the live page a second earlier.

    A spectator is attached **before** the model's turn runs, so the frames under test come down
    the live branch rather than the backfill — that is the branch a person actually watches. The
    game is then ended, because a live stream never closes on its own and httpx's ASGI transport
    delivers a response body only once it is complete.
    """
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())

    payloads: list[dict] = []

    async def watch() -> None:
        async with client.stream("GET", f"/games/{game_id}/stream") as response:
            assert response.status_code == 200, f"stream said {response.status_code}"
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    with contextlib.suppress(json.JSONDecodeError):
                        payloads.append(json.loads(line[5:].strip()))
                elif line.strip() == f"event: {EventType.GAME_ENDED}":
                    return

    reader = asyncio.create_task(watch())
    await asyncio.sleep(0.5)  # let the subscription attach before anything is published

    # The model thinks out loud and then resigns, so the frames under test and the `game_ended`
    # that closes the stream all come from the one published turn.
    await run_next(make_worker(_thinks_then_resigns(), publish=True), queue)

    try:
        await asyncio.wait_for(reader, timeout=15)
    except TimeoutError:
        reader.cancel()

    thinking = [f for f in payloads if f.get("type") == "thinking"]
    assert thinking, f"expected a thinking frame, got {[f.get('type') for f in payloads]}"
    assert all("reasoning" not in frame["payload"] for frame in thinking)
    # And the prose alongside it, which carries the same plan by another route.
    assert all("content" not in f["payload"] for f in payloads if f.get("type") == "output")


# ====================================================================== talking


async def test_a_message_reaches_the_model(client: AsyncClient, db: AsyncSession) -> None:
    game_id = await _new_game(client, db)

    response = await client.post(
        f"/games/{game_id}/say", json={"message": "good luck"}, headers=as_user()
    )
    assert response.status_code == 200

    messages = (await client.get(f"/games/{game_id}/messages")).json()
    assert [m["content"] for m in messages] == ["good luck"]


async def test_an_oversized_message_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    game_id = await _new_game(client, db)
    response = await client.post(
        f"/games/{game_id}/say", json={"message": "x" * 501}, headers=as_user()
    )
    assert response.status_code == 422


async def test_a_free_model_costs_no_credits(db: AsyncSession, client: AsyncClient) -> None:
    """Credits bound spend, and a `:free` model spends nothing — so charging a person to sit down
    against the cheapest thing on the site was charging for the wrong thing.

    Scoped to this endpoint. Pricing free models at zero in `ModelRegistry.credits` also opened
    `POST /games` to any signed-in account, because credits are what AUTH-11 uses to gate an
    unfunded user; two tests said so immediately.
    """
    await sync_model_registry(
        db,
        [
            {
                "openrouter_id": "test/opponent:free",
                "display_name": "Free Opponent",
                "is_free": True,
                "context_length": 200_000,
            }
        ],
    )
    await db.commit()

    response = await client.post(
        "/games/human",
        json={"model": "test/opponent:free", "colour": "white"},
        headers=as_user("user_unfunded_free", email="unfunded@chessmark.test"),
    )

    assert response.status_code == 201, response.text

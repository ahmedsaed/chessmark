"""Playing a model over HTTP (Phase 10).

These are the exit criteria that only hold end to end: that a crafted request bypassing the client
is refused, that a reload restores the game exactly, and that the model's reasoning stays private
until the game is over.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.db.enums import GameStatus
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


async def test_reasoning_is_withheld_while_a_human_game_is_live(
    client: AsyncClient, db: AsyncSession, queue, make_worker
) -> None:
    """HUMAN-07, and the sharpest case for invariant 8.

    In a model-vs-model game the reasoning leaks to spectators. Here the opponent *is* the
    spectator: a live reasoning trace would hand the person the model's plan.
    """
    game_id = await _new_game(client, db)
    await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    await run_next(make_worker(both_sides([], ["e5"])), queue)

    live = (await client.get(f"/games/{game_id}/events")).json()
    assert not any(e["type"] == "thinking" for e in live)

    turns = (await client.get(f"/games/{game_id}/turns")).json()
    assert all(not t.get("reasoning") for t in turns)

    # Once it is over, everything opens up (HUMAN-06).
    await client.post(f"/games/{game_id}/resign", headers=as_user())
    after = (await client.get(f"/games/{game_id}/turns")).json()
    assert after is not None


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

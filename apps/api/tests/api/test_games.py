"""Game endpoints: contract, and the reasoning-privacy invariant."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import plays, says, scripted, tool_call
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game
from tests.api.conftest import as_user, fund
from tests.orchestration.conftest import Fixture, both_sides, run_next

pytestmark = pytest.mark.integration


async def _play(db: AsyncSession, fixture: Fixture, make_worker, moves_white, moves_black, turns=4):
    worker = make_worker(both_sides(moves_white, moves_black))
    for _ in range(turns):
        if await run_next(worker, fixture.queue) is None:
            break
    return fixture


# ====================================================================== listing


async def test_listing_is_empty_before_any_game(client: AsyncClient) -> None:
    response = await client.get("/games")
    assert response.status_code == 200
    assert response.json() == []


async def test_a_game_appears_in_the_listing(client: AsyncClient, game: Fixture) -> None:
    response = await client.get("/games")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(game.game.id)
    assert body[0]["status"] == "running"
    assert [p["colour"] for p in body[0]["players"]] == ["black", "white"]


async def test_listing_filters_by_status(
    client: AsyncClient, db: AsyncSession, game: Fixture
) -> None:
    assert (await client.get("/games", params={"status": "running"})).json()
    assert (await client.get("/games", params={"status": "finished"})).json() == []


async def test_listing_rejects_an_absurd_limit(client: AsyncClient) -> None:
    assert (await client.get("/games", params={"limit": 9999})).status_code == 422


# ====================================================================== detail


async def test_game_detail_reports_the_live_position(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    await _play(db, game, make_worker, ["e4", "Nf3"], ["e5"], turns=3)

    body = (await client.get(f"/games/{game.game.id}")).json()

    assert body["moves"] == ["e4", "e5", "Nf3"]
    assert body["ply_count"] == 3
    assert body["current_fen"].startswith("rnbqkbnr/pppp1ppp")
    assert body["prompt_version"]
    assert body["tool_schema_version"]
    assert body["event_seq"] > 0


async def test_a_missing_game_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/games/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert "No game" in response.json()["detail"]


async def test_a_malformed_id_is_a_422(client: AsyncClient) -> None:
    assert (await client.get("/games/not-a-uuid")).status_code == 422


async def test_money_is_serialised_as_a_string(client: AsyncClient, game: Fixture) -> None:
    """Costs run to eight decimal places; a JSON float would round them (invariant 4)."""
    body = (await client.get(f"/games/{game.game.id}")).json()
    assert isinstance(body["total_cost_usd"], str)


# ====================================================================== plies


async def test_plies_are_returned_in_order(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    await _play(db, game, make_worker, ["e4", "Nf3"], ["e5", "Nc6"], turns=4)

    body = (await client.get(f"/games/{game.game.id}/plies")).json()

    assert [p["san"] for p in body] == ["e4", "e5", "Nf3", "Nc6"]
    assert [p["ply_number"] for p in body] == [1, 2, 3, 4]
    assert body[0]["colour"] == "white"
    # BENCH-08: the analysis columns exist and are null until Phase 14.
    assert body[0]["eval_after_cp"] is None
    assert body[0]["cp_loss"] is None


# ====================================================================== messages


async def test_messages_are_returned(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    worker = make_worker(scripted(says("Your move.", tool_call("make_move", move="e4"))))
    await run_next(worker, game.queue)

    body = (await client.get(f"/games/{game.game.id}/messages")).json()

    assert [m["content"] for m in body] == ["Your move."]


# ====================================================================== turns


async def test_turns_expose_tool_calls(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    await run_next(make_worker(plays(["e4"])), game.queue)

    body = (await client.get(f"/games/{game.game.id}/turns")).json()

    assert len(body) == 1
    assert body[0]["status"] == "completed"
    assert body[0]["tool_calls"][0]["name"] == "make_move"
    assert body[0]["tool_calls"][0]["arguments"] == {"move": "e4"}


# ====================================================================== reasoning privacy


async def test_reasoning_is_withheld_while_a_person_is_playing(
    client: AsyncClient, db: AsyncSession, queue, redis, make_worker
) -> None:
    """Invariant 8 / HUMAN-07. It would hand somebody their opponent's plan mid-decision."""
    from chessmark.agents.scripted import step
    from chessmark.game import Colour
    from tests.support import make_user, seat_human_match

    user = await make_user(db)
    human = await seat_human_match(db, queue, user=user, human_colour=Colour.BLACK)
    worker = make_worker(
        scripted(step(tool_call("make_move", move="e4"), reasoning="I intend Qh5 next."))
    )
    await run_next(worker, human.queue)

    body = (await client.get(f"/games/{human.game.id}/turns")).json()

    assert body[0]["reasoning_available"] is False
    assert body[0]["llm_calls"][0]["reasoning"] is None
    assert "Qh5" not in (await client.get(f"/games/{human.game.id}/turns")).text


async def test_reasoning_is_published_live_for_a_model_game(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """One rule, in one place.

    The event log has always streamed this game's `thinking` events live — there is no participant
    to leak to — while `/turns` applied a broader rule of its own and withheld the same text. Two
    gates in one module, disagreeing.
    """
    from chessmark.agents.scripted import step

    worker = make_worker(
        scripted(step(tool_call("make_move", move="e4"), reasoning="I intend Qh5 next."))
    )
    await run_next(worker, game.queue)

    body = (await client.get(f"/games/{game.game.id}/turns")).json()

    assert body[0]["reasoning_available"] is True
    assert "Qh5" in (await client.get(f"/games/{game.game.id}/turns")).text


async def test_reasoning_is_revealed_once_the_game_ends(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    from chessmark.agents.scripted import step

    worker = make_worker(scripted(step(tool_call("resign"), reasoning="This position is lost.")))
    await run_next(worker, game.queue)

    body = (await client.get(f"/games/{game.game.id}/turns")).json()

    assert body[0]["reasoning_available"] is True
    assert body[0]["llm_calls"][0]["reasoning"] == "This position is lost."


async def test_the_reasoning_was_stored_all_along(
    client: AsyncClient, db: AsyncSession, game: Fixture, make_worker
) -> None:
    """It is withheld, not discarded — LOG-01 still requires the verbatim record."""
    from chessmark.agents.scripted import step
    from chessmark.db.models import LlmCall

    worker = make_worker(scripted(step(tool_call("make_move", move="e4"), reasoning="secret plan")))
    await run_next(worker, game.queue)

    db.expunge_all()
    stored = (await db.scalars(sa.select(LlmCall))).all()
    assert stored[0].reasoning_text == "secret plan"


# ====================================================================== creation


async def test_creating_a_game_enqueues_it(client: AsyncClient, db: AsyncSession, queue) -> None:
    await sync_model_registry(
        db,
        [
            {"openrouter_id": "test/white", "display_name": "White Model"},
            {"openrouter_id": "test/black", "display_name": "Black Model"},
        ],
    )
    await db.commit()
    await fund(db, "user_enqueue")

    response = await client.post(
        "/games",
        json={"white": "test/white", "black": "test/black", "max_plies": 20},
        headers=as_user("user_enqueue"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "running"
    assert body["events_url"].endswith("/stream")

    db.expunge_all()
    created = await db.get(Game, __import__("uuid").UUID(body["id"]))
    assert created is not None
    assert created.status is GameStatus.RUNNING
    assert created.max_plies == 20


async def test_creating_with_an_unknown_model_is_rejected(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await client.post(
        "/games",
        json={"white": "nope/nope", "black": "nope/nope"},
        headers=as_user("user_unknown_model"),
    )

    assert response.status_code == 400
    assert "Unknown model" in response.json()["detail"]


async def test_a_model_without_tools_cannot_play(client: AsyncClient, db: AsyncSession) -> None:
    """AGENT-01: agents act only through tools, so this must fail loudly at creation."""
    await sync_model_registry(
        db,
        [
            {"openrouter_id": "test/mute", "display_name": "Mute", "supports_tools": False},
            {"openrouter_id": "test/ok", "display_name": "Ok"},
        ],
    )
    await db.commit()

    response = await client.post(
        "/games",
        json={"white": "test/mute", "black": "test/ok"},
        headers=as_user("user_no_tools"),
    )

    assert response.status_code == 400
    assert "tool calling" in response.json()["detail"]


async def test_a_ranked_game_is_forced_silent(client: AsyncClient, db: AsyncSession) -> None:
    """TALK-03: a ranked result contaminated by banter is not comparable."""
    await sync_model_registry(
        db,
        [
            {"openrouter_id": "test/a", "display_name": "A"},
            {"openrouter_id": "test/b", "display_name": "B"},
        ],
    )
    await db.commit()
    await fund(db, "user_ranked")

    response = await client.post(
        "/games",
        json={
            "white": "test/a",
            "black": "test/b",
            "is_ranked": True,
            "trash_talk_enabled": True,
        },
        headers=as_user("user_ranked"),
    )

    body = (await client.get(f"/games/{response.json()['id']}")).json()
    assert body["is_ranked"] is True
    assert body["trash_talk_enabled"] is False


# ====================================================================== models


async def test_the_model_registry_is_listed(client: AsyncClient, db: AsyncSession) -> None:
    await sync_model_registry(
        db,
        [
            {"openrouter_id": "free/one", "display_name": "One", "is_free": True},
            {"openrouter_id": "paid/two", "display_name": "Two", "is_free": False},
            {"openrouter_id": "mute/three", "display_name": "Three", "supports_tools": False},
        ],
    )
    await db.commit()

    # `playable=false` for the registry as stored: these fixtures have no endpoints, and the
    # default listing offers only models something can actually serve.
    everything = (await client.get("/models", params={"playable": False})).json()
    free = (await client.get("/models", params={"free_only": True, "playable": False})).json()

    slugs = {m["openrouter_id"] for m in everything}
    assert slugs == {"free/one", "paid/two"}, "tool-incapable models must not be offered"
    assert {m["openrouter_id"] for m in free} == {"free/one"}

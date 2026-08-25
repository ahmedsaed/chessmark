"""The games you are playing, as opposed to the ones you are watching (Phase 10).

`GET /games` is public and says nothing about who holds which seat — deliberately, since that
would publish it to every spectator. This is the private counterpart, and the reason a human game
is now reachable from the site instead of only from a URL you kept.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.api.routes.games import _side_to_move
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game
from chessmark.game import Colour
from tests.api.conftest import as_user, fund

pytestmark = pytest.mark.integration

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


async def _seed_model(db: AsyncSession) -> None:
    await sync_model_registry(
        db, [{"openrouter_id": "test/opponent", "display_name": "Opponent Model"}]
    )
    await db.commit()


async def _sit_down(client: AsyncClient, db: AsyncSession, *, colour: str = "white", **who) -> str:
    await _seed_model(db)
    # Sitting down costs credits (ADR-0016), and a fresh account holds none.
    await fund(db, who.get("clerk_user_id", "user_test"))
    response = await client.post(
        "/games/human",
        json={"model": "test/opponent", "colour": colour},
        headers=as_user(**who),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ====================================================================== whose games


async def test_listing_your_games_needs_an_account(client: AsyncClient) -> None:
    assert (await client.get("/games/mine")).status_code == 401


async def test_the_literal_route_is_not_swallowed_by_the_uuid_path(client: AsyncClient) -> None:
    """`/games/mine` must not be read as `/games/{game_id}`.

    Declaration order is the only thing keeping these apart, so this fails the moment someone
    moves the route below the detail one — which would answer 422 for a non-UUID path param.
    """
    response = await client.get("/games/mine", headers=as_user())
    assert response.status_code == 200, response.text


async def test_a_person_with_no_games_gets_an_empty_list(client: AsyncClient) -> None:
    assert (await client.get("/games/mine", headers=as_user())).json() == []


async def test_your_game_is_listed_with_the_seat_you_hold(
    client: AsyncClient, db: AsyncSession
) -> None:
    game_id = await _sit_down(client, db, colour="black")

    rows = (await client.get("/games/mine", headers=as_user())).json()
    assert [row["id"] for row in rows] == [game_id]
    assert rows[0]["your_colour"] == "black"


async def test_someone_elses_game_is_not_yours(client: AsyncClient, db: AsyncSession) -> None:
    """The seat is matched on user id, not on "there is a human seat".

    Without that, every signed-in person would see — and the UI would offer to resume — every
    human game on the deployment.
    """
    await _sit_down(client, db, clerk_user_id="user_one")

    rows = (await client.get("/games/mine", headers=as_user("user_two"))).json()
    assert rows == []


async def test_a_model_versus_model_game_belongs_to_nobody(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Starting a game is not the same as playing in one.

    The creator of a model-vs-model game holds no seat, so it must not appear in "your games"
    even though `games.created_by` points at them.
    """
    await _seed_model(db)
    await fund(db)
    response = await client.post(
        "/games",
        json={"white": "test/opponent", "black": "test/opponent"},
        headers=as_user(),
    )
    assert response.status_code == 201, response.text

    assert (await client.get("/games/mine", headers=as_user())).json() == []


# ====================================================================== whose turn


async def test_it_is_your_turn_when_you_have_white_and_nothing_has_been_played(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sit_down(client, db, colour="white")

    rows = (await client.get("/games/mine", headers=as_user())).json()
    assert rows[0]["your_turn"] is True


async def test_it_is_not_your_turn_when_the_model_has_white(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _sit_down(client, db, colour="black")

    rows = (await client.get("/games/mine", headers=as_user())).json()
    assert rows[0]["your_turn"] is False


async def test_your_turn_moves_to_the_model_after_you_play(
    client: AsyncClient, db: AsyncSession
) -> None:
    game_id = await _sit_down(client, db, colour="white")

    assert (
        await client.post(f"/games/{game_id}/moves", json={"move": "e4"}, headers=as_user())
    ).status_code == 200

    rows = (await client.get("/games/mine", headers=as_user())).json()
    assert rows[0]["ply_count"] == 1
    assert rows[0]["your_turn"] is False


async def test_a_finished_game_is_never_your_turn(client: AsyncClient, db: AsyncSession) -> None:
    """`your_turn` drives a "waiting on you" badge, and a game that is over is waiting on nobody."""
    game_id = await _sit_down(client, db, colour="white")

    assert (await client.post(f"/games/{game_id}/resign", headers=as_user())).status_code == 200

    row = await db.scalar(sa.select(Game).where(Game.id == game_id))
    await db.refresh(row)
    assert row.status is GameStatus.FINISHED

    rows = (await client.get("/games/mine", headers=as_user())).json()
    assert rows[0]["your_turn"] is False


# ====================================================================== the parity rule


def test_side_to_move_alternates_from_the_start_position() -> None:
    assert _side_to_move(START, 0) is Colour.WHITE
    assert _side_to_move(START, 1) is Colour.BLACK
    assert _side_to_move(START, 2) is Colour.WHITE


def test_side_to_move_honours_a_start_position_where_black_moves_first() -> None:
    """The start position is configurable (GAME-06), so parity alone is not the answer.

    This is the case that makes assuming White wrong, and it is cheap to get right.
    """
    black_first = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    assert _side_to_move(black_first, 0) is Colour.BLACK
    assert _side_to_move(black_first, 1) is Colour.WHITE

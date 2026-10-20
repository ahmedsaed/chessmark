"""The operator's pick for the lobby hero (ADR-0070): who may make it, what it may say, what it costs."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType
from chessmark.db.models import FeaturedGame, User
from chessmark.db.repositories import append_event, finish_game, rebuild_referee, record_ply
from chessmark.orchestration.queue import TurnQueue
from chessmark.orchestration.revalidation import FEATURED
from tests.api.conftest import as_user
from tests.support import Fixture, make_user, seat_human_match, seat_match

pytestmark = pytest.mark.integration

SCHOLAR = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
ADMIN = "user_featuring_admin"


async def play(db: AsyncSession, fixture: Fixture, sans: list[str], *, finish: bool) -> None:
    """Each turn thinks one line naming its move, and Black also leaves a note after Nf6."""
    game = fixture.game
    referee = await rebuild_referee(db, game)
    for san in sans:
        colour = referee.side_to_move
        await append_event(
            db,
            game_id=game.id,
            type=EventType.TURN_STARTED,
            payload={"ply": referee.ply + 1, "colour": str(colour)},
        )
        await append_event(
            db,
            game_id=game.id,
            type=EventType.THINKING,
            payload={"reasoning": f"I have decided on {san} after long thought."},
        )
        outcome = referee.play(san)
        await record_ply(db, game_id=game.id, colour=colour, move=outcome)
        await append_event(
            db, game_id=game.id, type=EventType.MOVE_MADE, payload={"san": outcome.move.san}
        )
        if san == "Nf6":
            await append_event(
                db,
                game_id=game.id,
                type=EventType.OUTPUT,
                payload={"content": "I did not check what Qxf7 does."},
            )
    if finish:
        assert referee.outcome is not None
        await finish_game(db, game_id=game.id, outcome=referee.outcome)
    await db.commit()


async def finished(db: AsyncSession, queue: TurnQueue) -> Fixture:
    fixture = await seat_match(db, queue)
    await play(db, fixture, SCHOLAR, finish=True)
    return fixture


async def make_admin(db: AsyncSession) -> None:
    db.add(User(clerk_user_id=ADMIN, email=f"{ADMIN}@test", is_admin=True))
    await db.commit()


async def feature(client: AsyncClient, game_id: object, **body: Any) -> Any:
    return await client.post(
        "/admin/featured",
        json={"game": str(game_id), "headline": "Mate in seven.", **body},
        headers=as_user(ADMIN),
    )


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    async def fake(tags: list[str], *, about: str = "") -> None:
        calls.append(tags)

    monkeypatch.setattr("chessmark.api.routes.admin.notify_tags", fake)
    return calls


async def test_there_is_no_pick_until_one_is_made(client: AsyncClient) -> None:
    response = await client.get("/games/featured")

    assert response.status_code == 200
    assert response.json() is None


async def test_a_featured_game_is_read_back_with_its_quotes_checked(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue, notified: list[list[str]]
) -> None:
    fixture = await finished(db, queue)
    await make_admin(db)

    response = await feature(
        client,
        fixture.game.id,
        white_quote="…I have decided on Qxf7# after long thought.",
        black_quote="I did not check what Qxf7 does.",
    )
    assert response.status_code == 200, response.text

    pick = (await client.get("/games/featured")).json()
    assert pick["game_id"] == str(fixture.game.id)
    assert pick["headline"] == "Mate in seven."
    assert pick["white_quote"] == {
        "text": "I have decided on Qxf7# after long thought.",
        "ply": 7,
        "san": "Qxf7#",
        "source": "reasoning",
    }
    assert pick["black_quote"]["ply"] == 6
    assert pick["black_quote"]["source"] == "output"
    assert notified == [[FEATURED]], "the lobby's cached pick must be told it changed"


async def test_a_misquote_is_refused_and_nothing_is_stored(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue, notified: list[list[str]]
) -> None:
    fixture = await finished(db, queue)
    await make_admin(db)

    response = await feature(client, fixture.game.id, black_quote="I saw Qxf7 coming.")

    assert response.status_code == 422
    assert "never wrote that" in response.json()["detail"]
    assert await db.scalar(sa.select(sa.func.count()).select_from(FeaturedGame)) == 0
    assert notified == []


async def test_a_game_still_being_played_cannot_be_featured(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """Its reasoning would be on the front page while it is still being decided (invariant 8)."""
    fixture = await seat_match(db, queue)
    await play(db, fixture, SCHOLAR[:4], finish=False)
    await make_admin(db)

    response = await feature(client, fixture.game.id)

    assert response.status_code == 422
    assert "Only a finished game" in response.json()["detail"]


async def test_a_game_a_person_played_cannot_be_featured(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    person = await make_user(db, "user_featured_person")
    fixture = await seat_human_match(db, queue, user=person)
    await play(db, fixture, SCHOLAR, finish=True)
    await make_admin(db)

    response = await feature(client, fixture.game.id)

    assert response.status_code == 422
    assert "A person played" in response.json()["detail"]


async def test_only_an_admin_can_feature_a_game(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    fixture = await finished(db, queue)

    response = await client.post(
        "/admin/featured",
        json={"game": str(fixture.game.id), "headline": "Mine now."},
        headers=as_user("user_not_an_admin"),
    )

    assert response.status_code == 403
    assert (await client.get("/games/featured")).json() is None


async def test_the_latest_pick_is_shown_and_removing_it_brings_back_the_one_before(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue, notified: list[list[str]]
) -> None:
    first = await finished(db, queue)
    second = await finished(db, queue)
    await make_admin(db)

    await feature(client, first.game.id, headline="The first.")
    await feature(client, second.game.id, headline="The second.")
    assert (await client.get("/games/featured")).json()["headline"] == "The second."

    removed = await client.delete(f"/admin/featured/{second.game.id}", headers=as_user(ADMIN))
    assert removed.status_code == 204
    assert (await client.get("/games/featured")).json()["headline"] == "The first."

    again = await client.delete(f"/admin/featured/{second.game.id}", headers=as_user(ADMIN))
    assert again.status_code == 404

    history = (await client.get("/admin/featured", headers=as_user(ADMIN))).json()
    assert [row["headline"] for row in history] == ["The second.", "The first."]
    assert history[0]["removed_at"] is not None


async def test_featuring_a_game_again_replaces_its_pick_rather_than_adding_one(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    fixture = await finished(db, queue)
    await make_admin(db)

    await feature(client, fixture.game.id, headline="Before.")
    await client.delete(f"/admin/featured/{fixture.game.id}", headers=as_user(ADMIN))
    await feature(client, fixture.game.id, headline="After.")

    assert (await client.get("/games/featured")).json()["headline"] == "After."
    assert await db.scalar(sa.select(sa.func.count()).select_from(FeaturedGame)) == 1


async def test_the_pick_costs_a_fixed_number_of_queries(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """The lobby calls this on every cache miss. It must not grow with the picks or the games."""
    await make_admin(db)
    fixture = await finished(db, queue)
    await feature(client, fixture.game.id)

    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        await client.get("/games/featured")
        one = len(statements)

        for _ in range(4):
            more = await finished(db, queue)
            await feature(client, more.game.id)
        statements.clear()

        await client.get("/games/featured")
        five = len(statements)
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert one <= 2, f"the pick took {one} queries"
    assert five == one, f"the pick grew from {one} to {five} queries with four more picks"

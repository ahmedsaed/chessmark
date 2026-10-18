"""`GET /games/{id}` carries the digest of a finished game, stored once (ADR-0069)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.digests import DIGEST_VERSION
from chessmark.db.enums import EventType
from chessmark.db.models import Game
from chessmark.db.repositories import append_event, finish_game, rebuild_referee, record_ply
from chessmark.game import Colour
from chessmark.orchestration.queue import TurnQueue
from tests.support import Fixture, make_user, seat_human_match, seat_match

pytestmark = pytest.mark.integration

SCHOLAR = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]


async def play(db: AsyncSession, fixture: Fixture, sans: list[str], *, finish: bool) -> None:
    """Write a game's moves and each turn's words the way the turn loop logs them."""
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
    if finish:
        assert referee.outcome is not None
        await finish_game(db, game_id=game.id, outcome=referee.outcome)
    await db.commit()


async def test_a_finished_game_states_its_opening_and_quotes_its_moves(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    fixture = await seat_match(db, queue)
    await play(db, fixture, SCHOLAR, finish=True)

    digest = (await client.get(f"/games/{fixture.game.id}")).json()["digest"]

    assert digest["version"] == DIGEST_VERSION
    assert digest["opening"]["name"] == "Bishop's Opening"
    final = digest["moments"][-1]
    assert (final["kind"], final["ply"], final["san"]) == ("final", 7, "Qxf7#")
    assert final["excerpt"] == "I have decided on Qxf7# after long thought."


async def test_the_digest_is_stored_and_not_recomputed(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """The game page is read live, so a digest recomputed per view would replay the game per view.

    Asserted on the statements, not on timing: the second read must not touch the plies at all.
    """
    fixture = await seat_match(db, queue)
    await play(db, fixture, SCHOLAR, finish=True)

    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        first = (await client.get(f"/games/{fixture.game.id}")).json()["digest"]
        computed = [s for s in statements if "game_events.payload" in s]
        statements.clear()
        second = (await client.get(f"/games/{fixture.game.id}")).json()["digest"]
        recomputed = [s for s in statements if "game_events.payload" in s]
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert computed, "the first read computes it"
    assert not recomputed, "the second read must find it stored"
    assert second == first

    stored = await db.scalar(sa.select(Game.digest).where(Game.id == fixture.game.id))
    assert stored is not None and stored["version"] == DIGEST_VERSION


async def test_a_digest_from_an_older_version_is_recomputed(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    fixture = await seat_match(db, queue)
    await play(db, fixture, SCHOLAR, finish=True)
    await db.execute(
        sa.update(Game)
        .where(Game.id == fixture.game.id)
        .values(digest={"version": DIGEST_VERSION - 1, "opening": None, "material": {}})
    )
    await db.commit()

    digest = (await client.get(f"/games/{fixture.game.id}")).json()["digest"]

    assert digest["version"] == DIGEST_VERSION
    assert digest["opening"] is not None


async def test_a_game_a_person_is_still_playing_has_no_digest(
    client: AsyncClient, db: AsyncSession, queue: TurnQueue
) -> None:
    """Invariant 8. A digest quotes reasoning, and a live opponent's reasoning is a plan."""
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user, human_colour=Colour.WHITE)
    await play(db, fixture, ["e4", "e5"], finish=False)

    body = (await client.get(f"/games/{fixture.game.id}")).json()

    assert body["digest"] is None
    assert "after long thought" not in str(body)
    stored = await db.scalar(sa.select(Game.digest).where(Game.id == fixture.game.id))
    assert stored is None, "nothing is computed for a live game either"

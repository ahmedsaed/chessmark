"""The person paying for a game can pause it, and only they can play it on (ADR-0052).

Through the API as they would, and then through the worker and the reconciler, because the pause
is asked for in one place and honoured in another: a running turn holds the game's row, so the
request waits for the worker, and a paused game must then stay paused whatever else comes due.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.pause_requests import PauseRequests
from chessmark.db.credits import grant
from chessmark.db.enums import EventType, GameStatus
from chessmark.db.models import Game, GameEvent, User
from chessmark.orchestration.match import Seat, create_match, start_match
from chessmark.orchestration.reconciler import reconcile, what_it_waits_for
from chessmark.orchestration.worker import (
    ADVANCED,
    NO_CREDIT,
    OWNER_PAUSE,
    OWNER_PAUSED,
    _is_our_stop,
)
from tests.api.conftest import as_user
from tests.support import both_sides, drain, run_next, seat_human_match

pytestmark = pytest.mark.integration

OWNER = "user_owner_pause"


class Spy:
    """A provider that must not be reached."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("the provider was called for a game its owner paused")


async def _owner(db: AsyncSession, usd: str = "1") -> User:
    user = User(clerk_user_id=OWNER, email=f"{OWNER}@chessmark.test", display_name="Owner")
    db.add(user)
    await db.flush()
    if Decimal(usd):
        await grant(db, user.id, Decimal(usd))
    await db.commit()
    return user


async def _game(db: AsyncSession, queue: Any, owner: User) -> Game:
    match = await create_match(
        db,
        white=Seat(display_name="white", model="vendor/white"),
        black=Seat(display_name="black", model="vendor/black"),
        created_by_user_id=owner.id,
    )
    job = await start_match(db, queue, game_id=match.game.id)
    await db.commit()
    await queue.enqueue(job)
    return match.game


async def _stored(sessionmaker: Any, game: Game) -> Game:
    async with sessionmaker() as session:
        stored = await session.get(Game, game.id)
        assert stored is not None
        return stored


# ====================================================================== pausing


async def test_a_running_game_pauses_before_its_next_turn(
    client: AsyncClient,
    db: AsyncSession,
    queue: Any,
    redis: Any,
    sessionmaker: Any,
    make_worker: Any,
) -> None:
    owner = await _owner(db)
    game = await _game(db, queue, owner)

    response = await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))
    assert response.status_code == 200, response.text
    # Asked for, not yet done: a turn may be holding the row, so the worker takes it from here.
    assert response.json() == {"status": "running", "pausing": True}

    spy = Spy()
    assert (await run_next(make_worker(spy), queue)).outcome == OWNER_PAUSED
    assert not spy.called

    stored = await _stored(sessionmaker, game)
    assert stored.status is GameStatus.PAUSED
    assert stored.pause_reason == OWNER_PAUSE
    assert not await PauseRequests(redis).pending(game.id)

    async with sessionmaker() as session:
        notice = await session.scalar(
            sa.select(GameEvent).where(
                GameEvent.game_id == game.id, GameEvent.type == EventType.GAME_PAUSED
            )
        )
        assert notice is not None
        assert notice.payload["by_owner"] is True
        assert _is_our_stop(notice.payload)  # kept off the abandonment clock


async def test_a_game_its_owner_paused_is_never_resumed_for_them(
    client: AsyncClient, db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    owner = await _owner(db)
    game = await _game(db, queue, owner)
    await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))
    await run_next(make_worker(Spy()), queue)
    await drain(queue)

    report = await reconcile(sessionmaker, queue)

    assert str(game.id) not in report.resumed
    async with sessionmaker() as session:
        waiting = await what_it_waits_for(session, await _stored(sessionmaker, game))
    assert waiting is not None
    assert waiting.kind == "owner"


async def test_a_game_paused_for_credit_is_held_for_its_owner_at_once(
    client: AsyncClient, db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    """No turn is in flight, so there is nothing to wait for — and adding credit afterwards must
    not bring back a game its owner has since chosen to hold."""
    owner = await _owner(db, usd="0")
    game = await _game(db, queue, owner)
    assert (await run_next(make_worker(Spy()), queue)).outcome == NO_CREDIT
    await drain(queue)

    response = await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))
    assert response.json() == {"status": "paused", "pausing": False}
    assert (await _stored(sessionmaker, game)).pause_reason == OWNER_PAUSE

    async with sessionmaker() as session:
        await grant(session, owner.id, Decimal(5))
        await session.commit()
    report = await reconcile(sessionmaker, queue)
    assert str(game.id) not in report.resumed


# ====================================================================== resuming


async def test_resuming_plays_on_from_the_same_position(
    client: AsyncClient, db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    owner = await _owner(db)
    game = await _game(db, queue, owner)
    await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))
    await run_next(make_worker(Spy()), queue)
    await drain(queue)

    response = await client.post(f"/games/{game.id}/resume", headers=as_user(OWNER))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"

    worker = make_worker(both_sides(["e4"], ["e5"]))
    assert (await run_next(worker, queue)).outcome == ADVANCED
    stored = await _stored(sessionmaker, game)
    assert stored.status is GameStatus.RUNNING
    assert stored.ply_count == 1


async def test_resuming_a_paid_game_needs_credit(
    client: AsyncClient, db: AsyncSession, queue: Any, make_worker: Any
) -> None:
    owner = await _owner(db, usd="0")
    game = await _game(db, queue, owner)
    await run_next(make_worker(Spy()), queue)  # paused for credit
    await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))

    response = await client.post(f"/games/{game.id}/resume", headers=as_user(OWNER))

    assert response.status_code == 402
    assert "$0.00" in response.text


async def test_resuming_before_the_pause_lands_withdraws_it(
    client: AsyncClient, db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    owner = await _owner(db)
    game = await _game(db, queue, owner)
    await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))

    await client.post(f"/games/{game.id}/resume", headers=as_user(OWNER))

    worker = make_worker(both_sides(["e4"], ["e5"]))
    assert (await run_next(worker, queue)).outcome == ADVANCED


# ====================================================================== who may


async def test_only_the_owner_may_pause(client: AsyncClient, db: AsyncSession, queue: Any) -> None:
    owner = await _owner(db)
    game = await _game(db, queue, owner)

    response = await client.post(f"/games/{game.id}/pause", headers=as_user("user_somebody_else"))

    assert response.status_code == 403


async def test_a_game_the_owner_plays_is_not_paused_this_way(
    client: AsyncClient, db: AsyncSession, queue: Any
) -> None:
    """The model moves only after they do; there is nothing to stop."""
    owner = await _owner(db)
    fixture = await seat_human_match(db, queue, user=owner, created_by_user_id=owner.id)

    response = await client.post(f"/games/{fixture.match.game.id}/pause", headers=as_user(OWNER))

    assert response.status_code == 409


async def test_a_game_that_ended_cannot_be_paused(
    client: AsyncClient, db: AsyncSession, queue: Any
) -> None:
    owner = await _owner(db)
    game = await _game(db, queue, owner)
    await db.execute(sa.update(Game).where(Game.id == game.id).values(status=GameStatus.FINISHED))
    await db.commit()

    response = await client.post(f"/games/{game.id}/pause", headers=as_user(OWNER))

    assert response.status_code == 409


# ====================================================================== who started it


async def test_a_game_names_the_person_who_started_it(
    client: AsyncClient, db: AsyncSession, queue: Any
) -> None:
    """A game outside any tournament says who ran it, as a tournament game says which event did —
    by display name, and never by the email the account also holds."""
    owner = await _owner(db)
    game = await _game(db, queue, owner)

    body = (await client.get(f"/games/{game.id}")).json()

    assert body["started_by"] == "Owner"
    assert body["tournament"] is None
    assert f"{OWNER}@chessmark.test" not in str(body)


async def test_an_unnamed_owner_is_not_named_by_their_email(
    client: AsyncClient, db: AsyncSession, queue: Any
) -> None:
    owner = await _owner(db)
    owner.display_name = None
    await db.commit()
    game = await _game(db, queue, owner)

    body = (await client.get(f"/games/{game.id}")).json()

    assert body["started_by"] == "a player"
    assert "@" not in body["started_by"]


async def test_a_game_nobody_started_names_nobody(client: AsyncClient, game: Any) -> None:
    body = (await client.get(f"/games/{game.match.game.id}")).json()

    assert body["started_by"] is None

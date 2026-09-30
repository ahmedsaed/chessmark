"""The house account pays for every game no person started (ADR-0058).

Tournaments and operator games used to charge nobody, while spending the same OpenRouter balance
that backs the credit users hold. With the house row present they are charged to it, pause when it
is empty, and resume when it is funded. A paid event holds rather than starting games it cannot
fund, and the sales headroom counts what the house holds as credit already promised.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db.capacity import headroom
from chessmark.db.credits import grant
from chessmark.db.enums import CreditReason, GameStatus
from chessmark.db.house import HOUSE_CLERK_ID
from chessmark.db.models import CreditLedger, Game, GameEvent, User
from chessmark.db.users import resolve_user
from chessmark.orchestration.reconciler import reconcile, what_it_waits_for
from chessmark.orchestration.tournament import advance
from chessmark.orchestration.worker import ADVANCED, CREDIT_PREFIX, NO_CREDIT
from chessmark.tournament import Format, TournamentConfig
from tests.support import Fixture, both_sides, drain, run_next
from tests.tournament.test_runner import make_tournament

pytestmark = pytest.mark.integration

TURN = Decimal("0.01")


async def _house(db: AsyncSession, usd: str) -> User:
    """The row the migration creates in every real database; the suite truncates it."""
    house = User(clerk_user_id=HOUSE_CLERK_ID, display_name="Chessmark")
    db.add(house)
    await db.flush()
    if Decimal(usd):
        await grant(db, house.id, Decimal(usd), note="test")
    await db.commit()
    return house


async def _balance(sessionmaker: Any, user: User) -> Decimal:
    async with sessionmaker() as session:
        return Decimal(await session.scalar(sa.select(User.balance_usd).where(User.id == user.id)))


class Spy:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("the provider was called for a game the house cannot fund")


async def test_a_game_nobody_started_is_charged_to_the_house(
    db: AsyncSession, game: Fixture, sessionmaker: Any, make_worker: Any
) -> None:
    house = await _house(db, "1")
    worker = make_worker(both_sides(["e4"], ["e5"], cost=float(TURN)))

    assert (await run_next(worker, game.queue)).outcome == ADVANCED

    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        rows = list(await session.scalars(sa.select(CreditLedger)))
    assert stored is not None and stored.total_cost_usd > 0
    turns = [row for row in rows if row.reason == CreditReason.TURN]
    assert [row.user_id for row in turns] == [house.id]
    assert -turns[0].delta == stored.total_cost_usd
    assert await _balance(sessionmaker, house) == Decimal(1) - stored.total_cost_usd
    assert stored.created_by_user_id is None, "nobody is named as having started it"


async def test_an_empty_house_pauses_the_game_and_funding_it_brings_it_back(
    db: AsyncSession, game: Fixture, sessionmaker: Any, queue: Any, make_worker: Any
) -> None:
    """Paused before the provider is reached, said to be waiting on Chessmark rather than on an
    owner nobody can name, and resumed by the first sweep after the house is funded."""
    house = await _house(db, "0")
    spy = Spy()

    assert (await run_next(make_worker(spy), queue)).outcome == NO_CREDIT
    assert not spy.called
    await drain(queue)

    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        assert stored is not None
        assert stored.status is GameStatus.PAUSED
        assert (stored.pause_reason or "").startswith(CREDIT_PREFIX)
        waiting = await what_it_waits_for(session, stored)
        assert waiting is not None and waiting.kind == "house_credit"
        notice = await session.scalar(sa.select(GameEvent).where(GameEvent.game_id == stored.id))
        assert notice is not None

    held = await reconcile(sessionmaker, queue)
    assert str(game.match.game.id) not in held.resumed

    async with sessionmaker() as session:
        await grant(session, house.id, Decimal(1))
        await session.commit()
    assert str(game.match.game.id) in (await reconcile(sessionmaker, queue)).resumed


async def test_a_paid_event_holds_while_the_house_is_empty(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: Any
) -> None:
    """Without the hold, a pool would keep starting games that pause at their first paid turn."""
    house = await _house(db, "0")
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    step = await advance(sessionmaker, queue, tournament_id=tournament_id)
    assert step.started == 0
    assert "house account is out of credit" in step.holding

    async with sessionmaker() as session:
        await grant(session, house.id, Decimal(5))
        await session.commit()
    step = await advance(sessionmaker, queue, tournament_id=tournament_id)
    assert step.holding == ""
    assert step.started == 1


async def test_a_free_event_never_holds_for_the_house(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: Any
) -> None:
    await _house(db, "0")
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1),
        free=True,
    )

    step = await advance(sessionmaker, queue, tournament_id=tournament_id)
    assert step.holding == ""
    assert step.started == 1


async def test_what_the_house_holds_is_not_for_sale(db: AsyncSession) -> None:
    """Its balance is credit already promised to tournaments, so it counts against the headroom
    exactly as a user's does (ADR-0056)."""
    await _house(db, "20")
    room = await headroom(db, openrouter_remaining=Decimal(50), house_reserve=Decimal(10))
    assert room.held == Decimal(20)
    assert room.available == Decimal(20)


async def test_the_house_is_found_by_name(db: AsyncSession) -> None:
    house = await _house(db, "0")
    found = await resolve_user(db, "house")
    assert found is not None and found.id == house.id

"""A game is paid for turn by turn, at what each turn cost, and pauses when its owner runs out.

ADR-0052, end to end through the worker and the reconciler: the charge is the turn's own cost, the
check before a turn is the only thing that stops play, and adding credit is enough to bring a
paused game back.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.credits import grant, ledger_total
from chessmark.db.enums import CreditReason, EventType, GameStatus
from chessmark.db.models import CreditLedger, Game, GameEvent, Turn, User
from chessmark.orchestration.match import Seat, create_match, start_match
from chessmark.orchestration.reconciler import reconcile, what_it_waits_for
from chessmark.orchestration.worker import ADVANCED, CREDIT_PREFIX, NO_CREDIT
from tests.support import Fixture, both_sides, drain, make_user, run_next

pytestmark = pytest.mark.integration

#: What each scripted turn costs. Round, so a balance can be written as a number of turns.
TURN = Decimal("0.01")


async def _owned_game(
    db: AsyncSession,
    queue: Any,
    owner: User,
    *,
    white: str = "vendor/white",
    black: str = "vendor/black",
) -> Fixture:
    match = await create_match(
        db,
        white=Seat(display_name="white", model=white),
        black=Seat(display_name="black", model=black),
        created_by_user_id=owner.id,
    )
    job = await start_match(db, queue, game_id=match.game.id)
    await db.commit()
    await queue.enqueue(job)
    return Fixture(match=match, first_job=job, queue=queue)


async def _funded(db: AsyncSession, usd: str, name: str = "user_payer") -> User:
    user = await make_user(db, name)
    if Decimal(usd):
        await grant(db, user.id, Decimal(usd), note="test")
    await db.commit()
    return user


async def _balance(sessionmaker: Any, user: User) -> Decimal:
    async with sessionmaker() as session:
        return Decimal(await session.scalar(sa.select(User.balance_usd).where(User.id == user.id)))


class Spy:
    """A provider that must not be reached."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("the provider was called for a game whose owner has no credit")


# ====================================================================== the charge


async def test_each_turn_is_charged_what_it_cost(
    db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    owner = await _funded(db, "1")
    game = await _owned_game(db, queue, owner)
    worker = make_worker(both_sides(["e4", "Nf3"], ["e5", "Nc6"], cost=float(TURN)))

    for _ in range(4):
        assert (await run_next(worker, queue)).outcome == ADVANCED

    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        rows = list(
            await session.scalars(
                sa.select(CreditLedger).where(CreditLedger.reason == CreditReason.TURN)
            )
        )
        turns = {t.id: t for t in await session.scalars(sa.select(Turn))}

        # **What the game says it cost is what its owner paid**, to the cent — the two are written
        # in one transaction and cannot disagree.
        assert stored is not None
        assert stored.total_cost_usd > 0
        assert sum(-row.delta for row in rows) == stored.total_cost_usd
        assert await _balance(sessionmaker, owner) == Decimal(1) - stored.total_cost_usd
        assert await ledger_total(session, owner.id) == Decimal(1) - stored.total_cost_usd

        # One row per turn, naming the turn it paid for, at that turn's own cost.
        assert len(rows) == 4
        for row in rows:
            assert row.game_id == stored.id
            assert row.turn_id in turns
            assert -row.delta == turns[row.turn_id].cost_usd


async def test_a_game_nobody_started_charges_nobody(
    db: AsyncSession, game: Fixture, sessionmaker: Any, make_worker: Any
) -> None:
    """A tournament's game, an operator's — no payer, and nothing on anyone's ledger."""
    worker = make_worker(both_sides(["e4"], ["e5"], cost=float(TURN)))

    assert (await run_next(worker, game.queue)).outcome == ADVANCED

    async with sessionmaker() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(CreditLedger)) == 0


# ====================================================================== pausing


async def test_an_owner_with_no_credit_pauses_the_game_before_any_call(
    db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    """The check is before the money is spent. Asserted the only way that means anything: the
    provider raises if it is reached at all."""
    owner = await _funded(db, "0")
    game = await _owned_game(db, queue, owner)
    spy = Spy()

    handled = await run_next(make_worker(spy), queue)

    assert handled.outcome == NO_CREDIT
    assert not spy.called
    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        assert stored is not None
        assert stored.status is GameStatus.PAUSED
        assert (stored.pause_reason or "").startswith(CREDIT_PREFIX)
        assert stored.ply_count == 0

        notice = await session.scalar(
            sa.select(GameEvent).where(GameEvent.type == EventType.GAME_PAUSED)
        )
        assert notice is not None
        assert notice.payload["credit_of"] == str(owner.id)

        # The page says what it waits for.
        waiting = await what_it_waits_for(session, stored)
        assert waiting is not None
        assert waiting.kind == "credit"


async def test_a_balance_overruns_by_the_turn_in_flight_and_then_stops(
    db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    """Half a turn of credit: the turn runs, since the balance was above zero; its cost takes the
    balance below zero; the next turn pauses. The overshoot is one turn and never more."""
    owner = await _funded(db, "0.005")
    game = await _owned_game(db, queue, owner)
    worker = make_worker(both_sides(["e4", "Nf3"], ["e5", "Nc6"], cost=float(TURN)))

    assert (await run_next(worker, queue)).outcome == ADVANCED
    assert await _balance(sessionmaker, owner) == Decimal("0.005") - TURN

    assert (await run_next(worker, queue)).outcome == NO_CREDIT
    assert await _balance(sessionmaker, owner) == Decimal("0.005") - TURN

    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        assert stored is not None
        assert stored.ply_count == 1


async def test_a_free_seat_is_never_paused_for_credit(
    db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    """A `:free` turn costs nothing, so it is never stopped for the lack of anything."""
    owner = await _funded(db, "0")
    await _owned_game(db, queue, owner, white="vendor/white:free", black="vendor/black:free")
    worker = make_worker(both_sides(["e4"], ["e5"]))

    assert (await run_next(worker, queue)).outcome == ADVANCED
    assert (await run_next(worker, queue)).outcome == ADVANCED
    assert await _balance(sessionmaker, owner) == 0


# ====================================================================== resuming


async def test_adding_credit_is_enough_to_bring_the_game_back(
    db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    """Held while the balance is empty, resumed on the first sweep after it is not — nothing has to
    find the game and restart it."""
    owner = await _funded(db, "0")
    game = await _owned_game(db, queue, owner)
    await run_next(make_worker(Spy()), queue)
    await drain(queue)

    held = await reconcile(sessionmaker, queue)
    assert str(game.match.game.id) not in held.resumed

    async with sessionmaker() as session:
        await grant(session, owner.id, Decimal(1))
        await session.commit()

    report = await reconcile(sessionmaker, queue)
    assert str(game.match.game.id) in report.resumed

    worker = make_worker(both_sides(["e4"], ["e5"], cost=float(TURN)))
    assert (await run_next(worker, queue)).outcome == ADVANCED
    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        assert stored is not None
        assert stored.status is GameStatus.RUNNING
        assert stored.ply_count == 1


async def test_a_credit_pause_is_not_charged_to_the_game_s_patience(db: AsyncSession) -> None:
    """The abandonment clock skips time the harness chose not to play. An empty balance is one of
    those — it is nothing the model did — and the marker is structural, as a halt's is."""
    from chessmark.orchestration.worker import _is_our_stop

    assert _is_our_stop({"credit_of": str(uuid.uuid4())})
    assert _is_our_stop({"halt_source": "credits"})
    assert not _is_our_stop({"limit_source": "provider"})
    assert not _is_our_stop(None)


async def test_a_decision_model_s_turn_is_charged_the_same_way(
    db: AsyncSession, queue: Any, sessionmaker: Any, make_worker: Any
) -> None:
    """A decision seat goes through its own runner (ADR-0049), and its runner charges too."""
    from chessmark.agents.scripted_decisions import deciding
    from tests.orchestration.test_decision_games import JEV, KEV, _register

    await _register(db)
    owner = await _funded(db, "1")
    game = await _owned_game(db, queue, owner, white=JEV, black=KEV)
    worker = make_worker(both_sides([], []), decide_fn=deciding(moves=["e4", "e5"], cost=0.002))

    assert (await run_next(worker, queue)).outcome == ADVANCED
    assert (await run_next(worker, queue)).outcome == ADVANCED

    async with sessionmaker() as session:
        stored = await session.get(Game, game.match.game.id)
        assert stored is not None
        assert stored.total_cost_usd == Decimal("0.004")
    assert await _balance(sessionmaker, owner) == Decimal("0.996")

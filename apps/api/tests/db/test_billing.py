"""A game is reconciled against what OpenRouter billed, and its payer settled to it (ADR-0054).

OpenRouter is faked at the HTTP layer, with the shapes production answered in: an analytics row per
generation with its cost **truncated to six places**, and an exact per-generation cost behind
`GET /generation`. The truncation is the trap — it read 1% low on every decision game — so the fake
keeps it, and the tests assert the exact figure wins.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.openrouter_billing import OpenRouterBilling
from chessmark.db import billing
from chessmark.db.billing import CHECKS, settle_billing
from chessmark.db.credits import grant, spend
from chessmark.db.enums import CreditReason, GameStatus, TurnStatus
from chessmark.db.models import CreditLedger, Game, LlmCall, Turn, UnrecordedGeneration, User
from chessmark.orchestration.worker import CREDIT_PREFIX, OWNER_PAUSE
from tests.support import Fixture, seat_match

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)


class FakeOpenRouter:
    """What OpenRouter holds for each session: generation id → exact cost."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Decimal]] = {}
        self.lookups: list[str] = []
        self.down = False

    def bill(self, game_id: uuid.UUID, generation: str, cost: str) -> None:
        self.sessions.setdefault(f"game-{game_id}", {})[generation] = Decimal(cost)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("/analytics/query"):
            body = json.loads(request.content)
            session = body["filters"][0]["value"]
            assert body["time_range"]["end"].endswith("Z"), "the API refuses +00:00"
            rows = [
                {
                    "generation_id": gid,
                    # Truncated, as the real API does: 0.000062496 reads 0.000062.
                    "total_usage": float(cost.quantize(Decimal("0.000001"), ROUND_DOWN)),
                }
                for gid, cost in self.sessions.get(session, {}).items()
            ]
            return httpx.Response(200, json={"data": {"data": rows, "metadata": {}}})
        gid = request.url.params["id"]
        self.lookups.append(gid)
        for generations in self.sessions.values():
            if gid in generations:
                return httpx.Response(200, json={"data": {"total_cost": str(generations[gid])}})
        return httpx.Response(404)

    def client(self) -> OpenRouterBilling:
        return OpenRouterBilling(
            management_key="mgmt",
            api_key="key",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.fixture
def openrouter() -> FakeOpenRouter:
    return FakeOpenRouter()


async def _payer(db: AsyncSession) -> User:
    user = User(clerk_user_id=f"user_{uuid.uuid4().hex[:8]}")
    db.add(user)
    await db.flush()
    await grant(db, user.id, Decimal(10))
    return user


async def _game(db: AsyncSession, queue: Any, payer: User | None) -> Fixture:
    fixture = await seat_match(
        db, queue, created_by_user_id=payer.id if payer is not None else None
    )
    return fixture


async def _played(
    db: AsyncSession, fixture: Fixture, calls: dict[str, str], *, charge: bool = True
) -> None:
    """Record calls as the turn loop does, charging each to the payer."""
    game = fixture.match.game
    turn = Turn(game_id=game.id, player_id=fixture.white.id, status=TurnStatus.COMPLETED)
    db.add(turn)
    await db.flush()
    for sequence, (gid, cost) in enumerate(calls.items(), start=1):
        db.add(
            LlmCall(
                game_id=game.id,
                turn_id=turn.id,
                sequence=sequence,
                model_slug="scripted/white",
                request={},
                response={"id": gid},
                cost_usd=Decimal(cost),
            )
        )
        if charge and game.created_by_user_id is not None:
            await spend(
                db, game.created_by_user_id, Decimal(cost), game_id=game.id, turn_id=turn.id
            )
    await db.flush()


async def _at_rest(db: AsyncSession, fixture: Fixture, **values: Any) -> Game:
    game = await db.get(Game, fixture.match.game.id)
    assert game is not None
    game.status = values.get("status", GameStatus.FINISHED)
    game.pause_reason = values.get("pause_reason")
    game.created_at = NOW - dt.timedelta(hours=1)
    await db.commit()
    return game


async def _sweep(sessionmaker: Any, openrouter: FakeOpenRouter, at: dt.datetime) -> Any:
    return await settle_billing(sessionmaker, openrouter.client(), now=at)


async def _stored(sessionmaker: Any, game_id: uuid.UUID) -> Game:
    async with sessionmaker() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        return game


async def _settlements(sessionmaker: Any, game_id: uuid.UUID) -> list[Decimal]:
    async with sessionmaker() as session:
        return list(
            await session.scalars(
                sa.select(CreditLedger.delta)
                .where(
                    CreditLedger.game_id == game_id,
                    CreditLedger.reason == CreditReason.SETTLEMENT,
                )
                .order_by(CreditLedger.id)
            )
        )


# ====================================================================== the total


async def test_the_billed_total_prices_what_we_lost_exactly(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    """Recorded calls keep their cost; one we never recorded is priced from its own generation,
    not from the analytics row, which truncates it."""
    fixture = await _game(db, queue, await _payer(db))
    game_id = fixture.match.game.id
    await _played(db, fixture, {"gen-a": "0.000062496", "gen-b": "0.00100000"})
    openrouter.bill(game_id, "gen-a", "0.000062496")
    openrouter.bill(game_id, "gen-b", "0.00100000")
    openrouter.bill(game_id, "gen-lost", "0.000046914")
    await _at_rest(db, fixture)

    await _sweep(sessionmaker, openrouter, NOW)  # first sight: the schedule starts
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])

    stored = await _stored(sessionmaker, game_id)
    assert stored.billed_usd == Decimal("0.00110941")
    assert stored.billed_requests == 3
    assert openrouter.lookups == ["gen-lost"], "only what we did not record is looked up"


async def test_a_lost_generation_is_looked_up_once(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    fixture = await _game(db, queue, await _payer(db))
    game_id = fixture.match.game.id
    openrouter.bill(game_id, "gen-lost", "0.002")
    await _at_rest(db, fixture)

    await _sweep(sessionmaker, openrouter, NOW)
    for delay in CHECKS:
        await _sweep(sessionmaker, openrouter, NOW + delay)

    assert openrouter.lookups == ["gen-lost"]
    async with sessionmaker() as session:
        stored = await session.get(UnrecordedGeneration, "gen-lost")
    assert stored is not None
    assert stored.cost_usd == Decimal("0.002")


async def test_a_silent_openrouter_changes_nothing_and_spends_no_check(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    """No answer is not an empty session. Reading it as one would refund a game its whole cost."""
    fixture = await _game(db, queue, await _payer(db))
    await _played(db, fixture, {"gen-a": "0.001"})
    await _at_rest(db, fixture)
    openrouter.down = True

    await _sweep(sessionmaker, openrouter, NOW)
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])

    stored = await _stored(sessionmaker, fixture.match.game.id)
    assert stored.billed_usd is None
    assert stored.billing_checks == 0
    assert await _settlements(sessionmaker, fixture.match.game.id) == []


# ====================================================================== settling the payer


async def test_the_payer_is_charged_what_openrouter_billed(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    """The owner's decision: a round our failure lost was still billed, so it is charged."""
    payer = await _payer(db)
    fixture = await _game(db, queue, payer)
    game_id = fixture.match.game.id
    await _played(db, fixture, {"gen-a": "0.003"})
    openrouter.bill(game_id, "gen-a", "0.003")
    openrouter.bill(game_id, "gen-lost", "0.002")
    await _at_rest(db, fixture)

    await _sweep(sessionmaker, openrouter, NOW)
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])

    assert await _settlements(sessionmaker, game_id) == [Decimal("-0.002")]
    async with sessionmaker() as session:
        balance = await session.scalar(sa.select(User.balance_usd).where(User.id == payer.id))
    assert balance == Decimal("9.995")


async def test_each_check_settles_only_what_changed(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    """Analytics lag: a generation can appear an hour late. The later check settles it; one that
    finds nothing new writes nothing."""
    fixture = await _game(db, queue, await _payer(db))
    game_id = fixture.match.game.id
    await _played(db, fixture, {"gen-a": "0.003"})
    openrouter.bill(game_id, "gen-a", "0.003")
    await _at_rest(db, fixture)

    await _sweep(sessionmaker, openrouter, NOW)
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])
    assert await _settlements(sessionmaker, game_id) == []

    openrouter.bill(game_id, "gen-late", "0.001")
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[1])
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[2])

    assert await _settlements(sessionmaker, game_id) == [Decimal("-0.001")]


async def test_a_game_recorded_above_its_bill_is_refunded(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    fixture = await _game(db, queue, await _payer(db))
    game_id = fixture.match.game.id
    await _played(db, fixture, {"gen-a": "0.003"})
    openrouter.bill(game_id, "gen-a", "0.0025")  # we recorded more than was billed
    await _at_rest(db, fixture)
    async with sessionmaker() as session, session.begin():
        # A recorded cost can only differ from the bill if it was written wrong; model it.
        await session.execute(
            sa.update(LlmCall).where(LlmCall.game_id == game_id).values(cost_usd=Decimal("0.0025"))
        )

    await _sweep(sessionmaker, openrouter, NOW)
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])

    assert await _settlements(sessionmaker, game_id) == [Decimal("0.0005")]


async def test_a_game_from_the_credits_era_is_recorded_not_charged(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    """Its turns were never charged in dollars (ADR-0052); settling it would bill a tester for a
    game their credits already paid."""
    fixture = await _game(db, queue, await _payer(db))
    game_id = fixture.match.game.id
    await _played(db, fixture, {"gen-a": "0.003"}, charge=False)
    openrouter.bill(game_id, "gen-a", "0.003")
    openrouter.bill(game_id, "gen-lost", "0.002")
    await _at_rest(db, fixture)

    await _sweep(sessionmaker, openrouter, NOW)
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])

    assert (await _stored(sessionmaker, game_id)).billed_usd == Decimal("0.005")
    assert await _settlements(sessionmaker, game_id) == []


# ====================================================================== when


async def test_the_schedule_is_three_checks_and_restarts_when_the_game_moves(
    db: AsyncSession, queue: Any, sessionmaker: Any, openrouter: FakeOpenRouter
) -> None:
    fixture = await _game(db, queue, await _payer(db))
    game_id = fixture.match.game.id
    await _at_rest(db, fixture, status=GameStatus.PAUSED, pause_reason=OWNER_PAUSE)

    await _sweep(sessionmaker, openrouter, NOW)
    assert (await _stored(sessionmaker, game_id)).billing_checks == 0, "not due at first sight"

    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])  # not due again yet
    assert (await _stored(sessionmaker, game_id)).billing_checks == 1

    await _sweep(sessionmaker, openrouter, NOW + CHECKS[1])
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[2])
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[2] * 3)
    assert (await _stored(sessionmaker, game_id)).billing_checks == len(CHECKS)

    # It resumed and played on: new events, so the schedule starts again.
    async with sessionmaker() as session, session.begin():
        await session.execute(
            sa.update(Game).where(Game.id == game_id).values(event_seq=Game.event_seq + 3)
        )
    later = NOW + CHECKS[2] * 4
    await _sweep(sessionmaker, openrouter, later)
    await _sweep(sessionmaker, openrouter, later + CHECKS[0])
    assert (await _stored(sessionmaker, game_id)).billing_checks == 1


@pytest.mark.parametrize(
    ("status", "reason", "reconciled"),
    [
        (GameStatus.PAUSED, OWNER_PAUSE, True),
        (GameStatus.PAUSED, f"{CREDIT_PREFIX} play resumes when credit is added", True),
        (GameStatus.PAUSED, "rate-limited by Poolside", False),  # lifts on its own
        (GameStatus.RUNNING, None, False),
        (GameStatus.ABORTED, None, True),
    ],
)
async def test_which_games_are_at_rest(
    db: AsyncSession,
    queue: Any,
    sessionmaker: Any,
    openrouter: FakeOpenRouter,
    status: GameStatus,
    reason: str | None,
    reconciled: bool,
) -> None:
    fixture = await _game(db, queue, await _payer(db))
    await _at_rest(db, fixture, status=status, pause_reason=reason)

    await _sweep(sessionmaker, openrouter, NOW)
    await _sweep(sessionmaker, openrouter, NOW + CHECKS[0])

    stored = await _stored(sessionmaker, fixture.match.game.id)
    assert (stored.billed_checked_at is not None) is reconciled


def test_the_pause_reasons_match_the_worker_s() -> None:
    """`db/` cannot import the worker, so the two strings are restated there — and held here."""
    assert billing.OWNER_PAUSE == OWNER_PAUSE
    assert billing.CREDIT_PREFIX == CREDIT_PREFIX


async def test_games_already_checked_do_not_block_the_ones_behind_them(
    db: AsyncSession,
    queue: Any,
    sessionmaker: Any,
    openrouter: FakeOpenRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first real run reached 100 of 169 games: the candidates were limited *before* asking
    which were due, so the ones checked once sat at the front for an hour and hid the rest."""
    monkeypatch.setattr(billing, "GAMES_PER_SWEEP", 1)  # candidates capped at five
    payer = await _payer(db)
    games = [await _game(db, queue, payer) for _ in range(7)]
    for index, fixture in enumerate(games):
        game = await _at_rest(db, fixture)
        # A fixed order, as production has: identical timestamps let Postgres return the rows in
        # any order, and a shuffled list reaches every game by chance.
        game.created_at = NOW - dt.timedelta(hours=1) + dt.timedelta(seconds=index)
        await db.commit()

    # Five minutes apart, as the loop runs: each sweep sees at most five candidates, and a game
    # first seen in one is due five minutes later.
    for minute in range(0, 40, 5):
        for _ in range(len(games)):
            await _sweep(sessionmaker, openrouter, NOW + dt.timedelta(minutes=minute))

    for fixture in games:
        assert (await _stored(sessionmaker, fixture.match.game.id)).billing_checks == 1

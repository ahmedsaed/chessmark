"""When an event may start a game: its hours, its daily cap, and the free-tier allowance.

The allowance is the one that matters. OpenRouter permits 1,000 free-model requests a day and
reports the count nowhere — no header, no endpoint — so the only way to stay under it is to count
our own attempts and stop early. Going over does not fail loudly: the provider starts refusing with
its own daily 429, which the gateway cannot tell apart from a hot shared pool, so it backs off
politely and then abandons the game. A pool would spend the rest of the day churning through
pairings and producing nothing.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.core.halt import SCOPE_FREE, Halt
from chessmark.db import tournaments as repo
from chessmark.db.models import Tournament
from chessmark.orchestration.tournament import advance, within_window
from chessmark.tournament import FieldFilter, Format, TournamentConfig
from tests.tournament.test_runner import make_tournament

pytestmark = pytest.mark.integration


# ====================================================================== the window


def test_a_daytime_window_is_open_during_the_day() -> None:
    morning, evening = dt.time(6), dt.time(20)

    assert within_window(morning, evening, dt.time(12))
    assert not within_window(morning, evening, dt.time(3))
    assert not within_window(morning, evening, dt.time(22))


def test_a_window_that_wraps_midnight_works() -> None:
    """22:00 to 04:00 means late evening through the small hours, not an empty range — which is
    what subtracting the two would have produced."""
    night, dawn = dt.time(22), dt.time(4)

    assert within_window(night, dawn, dt.time(23))
    assert within_window(night, dawn, dt.time(2))
    assert not within_window(night, dawn, dt.time(12))


def test_the_boundaries_are_half_open() -> None:
    """Inclusive at the start, exclusive at the end, so two adjacent windows cannot both be open."""
    assert within_window(dt.time(6), dt.time(20), dt.time(6))
    assert not within_window(dt.time(6), dt.time(20), dt.time(20))


def test_no_window_means_always() -> None:
    assert within_window(None, None, dt.time(3))
    assert within_window(dt.time(6), None, dt.time(3)), "half a window is not a window"


# ====================================================================== holding


async def test_an_event_outside_its_hours_starts_nothing(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )
    tournament = await db.get(Tournament, tournament_id)
    assert tournament is not None
    tournament.active_from = dt.time(6)
    tournament.active_until = dt.time(20)
    await db.commit()

    at_night = dt.datetime(2026, 1, 1, 3, 0, tzinfo=dt.UTC)
    step = await advance(sessionmaker, queue, tournament_id=tournament_id, now=at_night)
    db.expire_all()

    assert step.started == 0
    assert "outside its active hours" in step.holding
    assert len(await repo.in_flight(db, tournament_id)) == 0


async def test_the_same_event_plays_inside_its_hours(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )
    tournament = await db.get(Tournament, tournament_id)
    assert tournament is not None
    tournament.active_from = dt.time(6)
    tournament.active_until = dt.time(20)
    await db.commit()

    midday = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    step = await advance(sessionmaker, queue, tournament_id=tournament_id, now=midday)

    assert step.holding == ""
    assert step.started == 1


async def test_a_halt_stops_a_pool_scheduling_into_it(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue, redis
) -> None:
    """Starting a game under a halt produces one the worker can only answer with `halted`, and a
    pool that keeps scheduling fills its concurrency with games that cannot move (OPS-19)."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        free=True,
    )

    halt = Halt(redis)
    await halt.set("the free-model allowance for the day is spent (429)", scope=SCOPE_FREE)

    step = await advance(sessionmaker, queue, tournament_id=tournament_id, halt=halt)
    db.expire_all()

    assert step.started == 0
    assert "harness is halted" in step.holding
    assert len(await repo.in_flight(db, tournament_id)) == 0


async def test_a_paid_event_ignores_a_free_scoped_halt(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue, redis
) -> None:
    """The daily cap is a limit on the *free* distribution. A paid event never drew on it, and
    stopping one would be an outage for a limit it is not subject to."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1),
        free=False,
    )

    halt = Halt(redis)
    await halt.set("the free-model allowance for the day is spent (429)", scope=SCOPE_FREE)

    step = await advance(sessionmaker, queue, tournament_id=tournament_id, halt=halt)

    assert step.holding == ""
    assert step.started == 1


async def test_an_account_wide_halt_stops_a_paid_event_too(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue, redis
) -> None:
    """An empty account or an operator's stop covers everything, whatever it costs."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1),
        free=False,
    )

    halt = Halt(redis)
    await halt.set("our provider account is out of credits (402)")

    step = await advance(sessionmaker, queue, tournament_id=tournament_id, halt=halt)

    assert step.started == 0
    assert "harness is halted" in step.holding


async def test_a_daily_game_cap_holds_the_event(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """How an operator divides one account-wide allowance between several events."""
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )
    tournament = await db.get(Tournament, tournament_id)
    assert tournament is not None
    tournament.max_games_per_day = 1
    await db.commit()

    first = await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    assert first.started == 1

    await advance(sessionmaker, queue, tournament_id=tournament_id)  # settle nothing; game is live
    db.expire_all()
    second = await advance(sessionmaker, queue, tournament_id=tournament_id)

    assert second.started == 0
    assert "daily cap" in second.holding

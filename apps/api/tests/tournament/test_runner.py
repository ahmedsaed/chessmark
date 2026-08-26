"""Running a tournament end to end, against the Phase 13 exit criteria.

Every game here is played by a scripted provider, so the whole suite spends nothing. What is being
tested is the *runner*: that it schedules once, keeps within its bounds, settles results, resumes
without replaying, and stops when its budget is reached.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.registry import sync_model_registry
from chessmark.db import tournaments as repo
from chessmark.db.enums import GameStatus, TournamentStatus
from chessmark.db.models import (
    Game,
    ModelEndpoint,
    ModelRegistry,
    Tournament,
    TournamentEntrant,
    TournamentGame,
)
from chessmark.game import GameResult, Termination
from chessmark.orchestration.tournament import advance
from chessmark.tournament import FieldFilter, Format, TournamentConfig, standings

pytestmark = pytest.mark.integration


async def seed_models(db: AsyncSession, count: int, *, free: bool = False) -> list[str]:
    """`count` registered, playable models — each with an active tool-capable endpoint."""
    suffix = ":free" if free else ""
    slugs = [f"vendor/model-{i}{suffix}" for i in range(1, count + 1)]
    await sync_model_registry(
        db,
        [
            {
                "openrouter_id": slug,
                "display_name": slug,
                "context_length": 200_000,
                "supports_tools": True,
                "prompt_usd_per_token": "0.000001",
                "completion_usd_per_token": "0.000002",
            }
            for slug in slugs
        ],
    )
    await db.flush()

    for slug in slugs:
        model_id = await db.scalar(
            sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slug)
        )
        db.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name="TestProvider",
                quantization="fp8",
                context_length=200_000,
                supports_tools=True,
                is_active=True,
            )
        )
    await db.commit()
    return slugs


async def make_tournament(
    db: AsyncSession, *, models: int, config: TournamentConfig, free: bool = False
):
    await seed_models(db, models, free=free)
    entrants = await repo.resolve_field(db, config.field)
    tournament = await repo.create_tournament(
        db, name="Test Event", slug=f"test-{uuid.uuid4().hex[:8]}", config=config, entrants=entrants
    )
    created = tournament.id
    await db.commit()
    # The id, not the instance: the tests expire the session between steps to see what the runner
    # committed, and a live ORM object would then try to refresh itself outside async context.
    return created, entrants


async def finish_all_in_flight(db: AsyncSession, tournament_id: uuid.UUID, *, white_wins=True):
    """Mark every in-flight game finished, as a worker eventually would."""
    rows = await repo.in_flight(db, tournament_id)
    for row in rows:
        game = await db.get(Game, row.game_id)
        assert game is not None
        game.status = GameStatus.FINISHED
        game.result = GameResult.WHITE_WINS if white_wins else GameResult.DRAW
        game.termination = Termination.CHECKMATE
        game.ply_count = 20
    await db.commit()
    return len(rows)


# ====================================================================== field selection


async def test_the_field_is_a_filter_not_a_hard_coded_list(db: AsyncSession) -> None:
    """The whole point of the design: one machine, many brackets."""
    await seed_models(db, 3, free=True)
    await seed_models(db, 4, free=False)

    free = await repo.resolve_field(db, FieldFilter(free_only=True))
    paid = await repo.resolve_field(db, FieldFilter(free_only=False))
    everyone = await repo.resolve_field(db, FieldFilter())

    assert len(free) == 3
    assert len(paid) == 4
    assert len(everyone) == 7


async def test_a_model_with_no_live_endpoint_is_never_entered(db: AsyncSession) -> None:
    """It has no contestants and cannot be played (ADR-0015). Entering it would schedule games
    that forfeit at ply 0 and put a loss on whoever it was paired against."""
    slugs = await seed_models(db, 2)
    await db.execute(
        sa.update(ModelEndpoint)
        .where(
            ModelEndpoint.model_id.in_(
                sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slugs[0])
            )
        )
        .values(is_active=False)
    )
    await db.commit()

    field = await repo.resolve_field(db, FieldFilter())

    assert [e.key for e in field] == [slugs[1]]


async def test_the_limit_caps_the_field(db: AsyncSession) -> None:
    await seed_models(db, 6)

    field = await repo.resolve_field(db, FieldFilter(limit=4))

    assert len(field) == 4


# ====================================================================== the exit criteria


async def test_eight_models_played_twice_schedules_fifty_six_games(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """The headline criterion, at the scheduling level: the whole fixture list, written down."""
    tournament_id, _ = await make_tournament(
        db,
        models=8,
        config=TournamentConfig(format=Format.ROUND_ROBIN, double=True, max_concurrent=2),
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)

    scheduled = await db.scalar(
        sa.select(sa.func.count(TournamentGame.id)).where(
            TournamentGame.tournament_id == tournament_id
        )
    )
    assert scheduled == 56


async def test_a_round_robin_is_scheduled_once_and_never_again(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Rescheduling would duplicate the whole fixture list on every tick."""
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN)
    )

    for _ in range(3):
        await advance(sessionmaker, queue, tournament_id=tournament_id)

    scheduled = await db.scalar(
        sa.select(sa.func.count(TournamentGame.id)).where(
            TournamentGame.tournament_id == tournament_id
        )
    )
    assert scheduled == 6, "four models meet once each"


async def test_concurrency_is_bounded(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """One is not a placeholder for free models: the daily allowance is consumed at about the
    rate a single game generates it, so a second in flight just exhausts the day sooner."""
    tournament_id, _ = await make_tournament(
        db, models=6, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    for _ in range(4):
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        assert len(await repo.in_flight(db, tournament_id)) <= 1


async def test_a_crash_resumes_without_replaying_completed_games(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """The criterion, and the reason nothing is held in process memory.

    `advance` is stateless between calls: everything it needs is written down. So "resuming" is
    just calling it again — there is no recovery path to get wrong.
    """
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=2)
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    finished = await finish_all_in_flight(db, tournament_id)
    assert finished == 2

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    settled_first = {
        (r.white_key, r.black_key)
        for r in await db.scalars(
            sa.select(TournamentGame).where(
                TournamentGame.tournament_id == tournament_id,
                TournamentGame.white_score.is_not(None),
            )
        )
    }

    # A fresh "process" — a new call, holding nothing — must not replay those.
    for _ in range(6):
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        await finish_all_in_flight(db, tournament_id)
        db.expire_all()

    games_per_pairing = await db.execute(
        sa.select(
            TournamentGame.white_key, TournamentGame.black_key, sa.func.count(TournamentGame.id)
        )
        .where(TournamentGame.tournament_id == tournament_id)
        .group_by(TournamentGame.white_key, TournamentGame.black_key)
    )
    assert all(count == 1 for *_, count in games_per_pairing), "a pairing was scheduled twice"
    assert settled_first, "the first two results should have been recorded"


async def test_the_budget_stops_the_event_and_says_so(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Checked before starting anything: noticing afterwards means the money is already gone."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(
            format=Format.ROUND_ROBIN, max_concurrent=1, max_usd=Decimal("0.10")
        ),
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    rows = await repo.in_flight(db, tournament_id)
    game = await db.get(Game, rows[0].game_id)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.result = GameResult.DRAW
    game.total_cost_usd = Decimal("0.50")  # blew through the ceiling in one game
    await db.commit()

    step = await advance(sessionmaker, queue, tournament_id=tournament_id)

    assert step.status is TournamentStatus.PAUSED
    assert "budget" in step.detail
    db.expire_all()
    assert len(await repo.in_flight(db, tournament_id)) == 0, "nothing new may start"


async def test_spend_is_summed_from_the_games_not_accumulated(db: AsyncSession) -> None:
    """A running total drifts; a sum over the call log cannot (invariant 4)."""
    tournament_id, _ = await make_tournament(
        db, models=2, config=TournamentConfig(format=Format.ROUND_ROBIN)
    )
    assert await repo.spent(db, tournament_id) == Decimal(0)


# ====================================================================== results


async def test_a_finished_game_settles_its_pairing(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    tournament_id, _ = await make_tournament(
        db, models=2, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    await finish_all_in_flight(db, tournament_id, white_wins=True)

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    results = await repo.results_so_far(db, tournament_id)
    assert [r.white_score for r in results] == [1.0]


async def test_an_abandoned_game_is_not_a_result_about_either_player(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """A provider we could not reach is our failure, not a finding. Scoring it would put a loss
    on a record for something the model never did."""
    tournament_id, _ = await make_tournament(
        db, models=2, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    rows = await repo.in_flight(db, tournament_id)
    # Read out before expiring: an expired ORM instance reloads itself on attribute access, which
    # cannot happen outside the async context.
    pairing_id = rows[0].id
    game = await db.get(Game, rows[0].game_id)
    assert game is not None
    game.status = GameStatus.ABORTED
    game.termination_detail = "no provider could be reached"
    await db.commit()

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    assert await repo.results_so_far(db, tournament_id) == []
    row = await db.get(TournamentGame, pairing_id)
    assert row is not None and row.abandoned_reason


async def test_a_completed_event_reports_finished_and_a_full_table(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    tournament_id, entrants = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=3)
    )

    for _ in range(10):
        step = await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        if step.status is TournamentStatus.FINISHED:
            break
        await finish_all_in_flight(db, tournament_id)
        db.expire_all()

    db.expire_all()
    refreshed = await db.get(Tournament, tournament_id)
    assert refreshed is not None and refreshed.status is TournamentStatus.FINISHED

    table = standings(entrants, await repo.results_so_far(db, tournament_id))
    assert sum(s.played for s in table) == 12, "six games, two seats each"


# ====================================================================== a whole event


async def test_a_double_round_robin_runs_to_completion_unattended(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """The headline criterion: 56 games, no operator, and it stops by itself.

    Driven by repeated `advance` calls, which is exactly what a supervised worker or a cron tick
    would do — the runner is not given a loop of its own precisely so that the caller can be
    anything. Games are settled here rather than played, because what is under test is the
    scheduler: that every pairing runs once, concurrency holds, and the event terminates.
    """
    tournament_id, entrants = await make_tournament(
        db,
        models=8,
        config=TournamentConfig(format=Format.ROUND_ROBIN, double=True, max_concurrent=4),
    )

    ticks = 0
    for ticks in range(1, 200):  # noqa: B007 - the bound is a guard, not the loop's purpose
        step = await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        if step.status is TournamentStatus.FINISHED:
            break
        assert len(await repo.in_flight(db, tournament_id)) <= 4, "concurrency bound broken"
        await finish_all_in_flight(db, tournament_id)
        db.expire_all()
    else:  # pragma: no cover - only on a runner that never terminates
        raise AssertionError("the tournament never finished")

    tournament = await db.get(Tournament, tournament_id)
    assert tournament is not None and tournament.status is TournamentStatus.FINISHED

    results = await repo.results_so_far(db, tournament_id)
    assert len(results) == 56, "every pairing played exactly once"

    table = standings(entrants, results)
    assert sum(s.played for s in table) == 112, "56 games, two seats each"
    assert all(s.played == 14 for s in table), "each model meets seven others twice"
    assert sum(s.score for s in table) == 56.0, "one point is awarded per game"


async def test_a_swiss_event_plays_its_configured_rounds_and_stops(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Swiss is scheduled a round at a time, so "finished" means the round count is reached —
    not that every pairing exists, because most of them never will."""
    tournament_id, entrants = await make_tournament(
        db,
        models=8,
        config=TournamentConfig(format=Format.SWISS, rounds=3, max_concurrent=4),
    )

    for _ in range(60):
        step = await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        if step.status is TournamentStatus.FINISHED:
            break
        await finish_all_in_flight(db, tournament_id)
        db.expire_all()
    else:  # pragma: no cover
        raise AssertionError("the Swiss event never finished")

    results = await repo.results_so_far(db, tournament_id)
    assert len(results) == 12, "three rounds of four games"

    table = standings(entrants, results)
    assert all(s.played == 3 for s in table), "everyone plays every round"
    assert max(s.score for s in table) == 3.0, "an undefeated leader emerges"


# ====================================================================== managing an event


async def test_a_paused_event_stays_paused(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Without this it restarts on the next tick — including one its own budget stopped, which
    would then spend straight past the ceiling it had just halted at."""
    tournament_id, _ = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    tournament = await db.get(Tournament, tournament_id)
    assert tournament is not None
    tournament.status = TournamentStatus.PAUSED
    await db.commit()

    before = len(await repo.unplayed(db, tournament_id))
    step = await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    assert step.status is TournamentStatus.PAUSED
    assert step.started == 0, "a paused event must not start anything"
    assert len(await repo.unplayed(db, tournament_id)) == before


async def test_a_withdrawn_entrant_is_dropped_from_future_pairings(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Their played games stand — those are real results — but nothing new is scheduled for them.

    The unplayed pairings are abandoned rather than awarded: a walkover is not a finding about the
    opponent, and handing out free points would distort the table more than a missing game does.
    """
    tournament_id, entrants = await make_tournament(
        db, models=4, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    leaving = entrants[0].key
    row = await db.scalar(
        sa.select(TournamentEntrant).where(
            TournamentEntrant.tournament_id == tournament_id, TournamentEntrant.key == leaving
        )
    )
    assert row is not None
    row.withdrawn = True
    for pairing in await repo.unplayed(db, tournament_id):
        if leaving in (pairing.white_key, pairing.black_key):
            pairing.abandoned_reason = f"{leaving} withdrew"
    await db.commit()
    db.expire_all()

    remaining = await repo.entrants_of(db, tournament_id)
    assert leaving not in {e.key for e in remaining}

    results = await repo.results_so_far(db, tournament_id)
    assert all(leaving not in (r.white, r.black) for r in results), "no walkover was awarded"

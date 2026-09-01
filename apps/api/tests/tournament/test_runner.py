"""Running a tournament end to end, against the Phase 13 exit criteria.

Every game here is played by a scripted provider, so the whole suite spends nothing. What is being
tested is the *runner*: that it schedules once, keeps within its bounds, settles results, resumes
without replaying, and stops when its budget is reached.
"""

from __future__ import annotations

import datetime as dt
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
from chessmark.orchestration.tournament import (
    DEAD_ATTEMPTS,
    DEAD_REST,
    _fruitless_entrants,
    advance,
)
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
        # Idempotent: a pool test grows the catalogue by calling this again with a larger count,
        # and the endpoint table is unique on (model, provider).
        already = await db.scalar(
            sa.select(ModelEndpoint.id).where(
                ModelEndpoint.model_id == model_id,
                ModelEndpoint.provider_name == "TestProvider",
            )
        )
        if already is None:
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


async def test_a_stale_score_is_corrected_rather_than_believed_for_ever(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """The half that made the display bug a *standings* bug.

    A resumed game's pairing kept the score of the forfeit that had just been overturned, and
    `_settle_finished` skipped anything already scored — so when the game then drew by threefold
    repetition at ply 100, the real result had nowhere to go. The standings recorded a loss where
    there was a draw, and no later tick would ever correct it. Observed in production, in a pool
    whose leader was a half-point better off than it had earned.
    """
    tournament_id, _ = await make_tournament(
        db, models=3, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    rows = await repo.in_flight(db, tournament_id)
    pairing_id, game_id = rows[0].id, rows[0].game_id

    # The state a resume used to leave behind: a score from the overturned verdict, on a game that
    # is playing again.
    pairing = await db.get(TournamentGame, pairing_id)
    assert pairing is not None
    pairing.white_score = 1.0
    await db.commit()

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    pairing = await db.get(TournamentGame, pairing_id)
    assert pairing is not None
    assert pairing.white_score is None, "a game in flight is not a decided pairing"

    # And when it does finish, the game's own result is what lands.
    game = await db.get(Game, game_id)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.result = GameResult.DRAW
    game.termination = Termination.THREEFOLD_REPETITION
    game.ply_count = 100
    await db.commit()

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    pairing = await db.get(TournamentGame, pairing_id)
    assert pairing is not None
    assert pairing.white_score == 0.5, "the threefold draw, not the forfeit it replaced"


async def test_settling_is_idempotent(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """`_settle_finished` now offers every pairing on every tick, so `settle` must report a change
    only when it makes one. Otherwise the count a tick reports climbs for ever and reads as work."""
    tournament_id, _ = await make_tournament(
        db, models=3, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    await finish_all_in_flight(db, tournament_id, white_wins=True)

    first = await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    assert first.settled == 1

    second = await advance(sessionmaker, queue, tournament_id=tournament_id)
    assert second.settled == 0, "nothing changed the second time, so nothing is reported"


async def test_a_resumed_game_settles_even_though_its_pairing_was_abandoned(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """An abandonment is a verdict, and a resumed game can outgrow it.

    `_settle_finished` used to skip any pairing already carrying an `abandoned_reason`, on the
    reasonable-sounding ground that it was settled. But a game abandoned on a provider 404 was
    resumed, played on to checkmate at ply 120 — and its pairing stayed "abandoned, no score" for
    ever, invisible to the standings. Nothing could ever leave that state.

    Three models, not two: with two the single pairing completes the event, `advance` returns
    "already over" on the next tick and never reaches the settle at all — which is a real
    limitation of resuming a *closed* event, and not the one under test here. A pool, which is
    where this was seen, never finishes.
    """
    tournament_id, _ = await make_tournament(
        db, models=3, config=TournamentConfig(format=Format.ROUND_ROBIN, max_concurrent=1)
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    rows = await repo.in_flight(db, tournament_id)
    pairing_id, game_id = rows[0].id, rows[0].game_id

    # Abandoned, and settled as such.
    game = await db.get(Game, game_id)
    assert game is not None
    game.status = GameStatus.ABORTED
    game.termination_detail = "provider returned 404"
    await db.commit()
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    pairing = await db.get(TournamentGame, pairing_id)
    assert pairing is not None and pairing.abandoned_reason, "the abandonment was recorded"

    # Resumed by hand, and played to a real result.
    game = await db.get(Game, game_id)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.result = GameResult.BLACK_WINS
    game.termination = Termination.CHECKMATE
    game.ply_count = 120
    await db.commit()

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    pairing = await db.get(TournamentGame, pairing_id)
    assert pairing is not None
    assert pairing.white_score == 0.0, "the checkmate is the result, and it is black's"
    assert pairing.abandoned_reason is None, "a real result overrides the abandonment it outgrew"
    assert any(r.white_score == 0.0 for r in await repo.results_so_far(db, tournament_id)), (
        "and it reaches the standings, which was the whole complaint"
    )


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


# ====================================================================== pools


async def test_a_pool_admits_a_model_listed_after_it_started(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """The whole reason pools exist.

    A closed event's field is frozen because its fixture list is computed from it. A pool has no
    fixture list, and Glicko-2 is built for an open population — so a model listed today starts at
    1500 ± 350 and settles by playing, rather than waiting for the next event.
    """
    tournament_id, _ = await make_tournament(
        db,
        models=3,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    assert len(await repo.entrants_of(db, tournament_id)) == 3

    await seed_models(db, 5)  # two more appear in the catalogue
    db.expire_all()

    step = await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    assert len(step.admitted) == 2
    assert len(await repo.entrants_of(db, tournament_id)) == 5


async def test_a_pool_never_finishes(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """There is no round count to reach and no fixture list to exhaust."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    for _ in range(8):
        step = await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        assert step.status is not TournamentStatus.FINISHED
        await finish_all_in_flight(db, tournament_id)
        db.expire_all()

    results = await repo.results_so_far(db, tournament_id)
    assert len(results) >= 4, "it kept finding games to play"


async def test_a_pool_pairs_only_what_it_can_run(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Scheduling ahead would freeze the matchmaker's information at the moment it was written —
    and a pool always has another fixture available, so the queue would grow without limit."""
    tournament_id, _ = await make_tournament(
        db,
        models=6,
        config=TournamentConfig(format=Format.POOL, max_concurrent=2, field=FieldFilter()),
    )

    for _ in range(3):
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        pending = len(await repo.unplayed(db, tournament_id)) + len(
            await repo.in_flight(db, tournament_id)
        )
        assert pending <= 2, f"{pending} games pending against a bound of 2"


async def test_a_pool_stops_at_its_budget_like_any_other_event(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """A pool has no end, so the ceiling is the only thing that ever stops it."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(
            format=Format.POOL,
            max_concurrent=1,
            max_usd=Decimal("0.10"),
            field=FieldFilter(),
        ),
    )
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    rows = await repo.in_flight(db, tournament_id)
    game = await db.get(Game, rows[0].game_id)
    assert game is not None
    game.status = GameStatus.FINISHED
    game.result = GameResult.DRAW
    game.total_cost_usd = Decimal("0.50")
    await db.commit()

    step = await advance(sessionmaker, queue, tournament_id=tournament_id)

    assert step.status is TournamentStatus.PAUSED
    assert "budget" in step.detail


# ====================================================================== rate-limited entrants
#
# The failure this section exists for: a pool with a concurrency of one, and one entrant whose only
# endpoint is rate-limited. Fourteen consecutive games died at ply 0 because neither half of the
# system knew — the matchmaker kept choosing the model it knew least about, and it stayed the model
# it knew least about precisely *because* its games kept failing.


async def test_a_paused_game_does_not_hold_the_concurrency_slot(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Concurrency counts games that can actually move.

    A paused game is waiting on a clock, not on a worker, and it is not spending: holding the slot
    would stop a pool with `max_concurrent=1` dead for as long as one provider was hot, which is
    exactly what happened. Deliberately looser than "never more than one game exists" — it means
    "never more than one game is *running*".
    """
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    first = await repo.in_flight(db, tournament_id)
    assert len(first) == 1

    paused_id = first[0].game_id
    paused = await db.get(Game, paused_id)
    assert paused is not None
    paused.status = GameStatus.PAUSED
    paused.pause_reason = "rate-limited upstream"
    await db.commit()

    # The slot is free, so the next tick starts something else rather than holding.
    step = await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    assert step.started == 1
    running = await repo.in_flight(db, tournament_id)
    assert len(running) == 1, "one *running* game, and the paused one is not it"
    assert running[0].game_id != paused_id


async def test_a_resting_entrant_is_not_paired(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue, redis
) -> None:
    """The half that broke the loop. Without it the pool re-pairs the model it cannot play, forever,
    because an abandoned game leaves the rating untouched and the tie-break is alphabetical."""
    from chessmark.core.cooldown import ProviderCooldown

    tournament_id, entrants = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )
    # `.key`, not the `Entrant` itself. Passing the object cooled down a `repr()` and made the
    # assertion below unfalsifiable — an `Entrant` is never equal to a `str`, so it passed while
    # testing nothing at all.
    resting = entrants[0].key
    cooldown = ProviderCooldown(redis)
    await cooldown.note(resting, provider="TestProvider")

    await advance(sessionmaker, queue, tournament_id=tournament_id, cooldown=cooldown)
    db.expire_all()

    rows = await repo.in_flight(db, tournament_id)
    assert len(rows) == 1
    assert resting not in {rows[0].white_key, rows[0].black_key}


async def test_without_a_cooldown_the_pool_behaves_as_it_always_did(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Optional everywhere, so a closed event and a test suite need not wire one."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    step = await advance(sessionmaker, queue, tournament_id=tournament_id, cooldown=None)

    assert step.started == 1


async def test_a_pool_starts_another_game_while_others_are_paused(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """A paused game must not stall the pool, and for a while it did.

    There used to be a ceiling here — a pool stopped starting games once `max_concurrent + 2` were
    paused — on the reasoning that a hot provider would otherwise be absorbed by opening game after
    game. It stalled the pool completely instead: three paused games against a concurrency of one
    meant nothing running and nothing starting, which is the opposite of what freeing the slot was
    for.

    The failure it guarded against is handled one layer down by something that actually knows which
    providers are hot — the cooldown, which stops the matchmaker pairing them at all. So the pool
    holds when there is no game worth starting, rather than when a number says so.
    """
    tournament_id, _ = await make_tournament(
        db,
        models=8,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    started: list[uuid.UUID] = []
    for _ in range(4):
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()

        rows = await repo.in_flight(db, tournament_id)
        if not rows:
            break
        for row in rows:
            paused_game = await db.get(Game, row.game_id)
            assert paused_game is not None
            paused_game.status = GameStatus.PAUSED
            paused_game.pause_reason = "rate-limited upstream"
            started.append(row.game_id)
        await db.commit()

    assert len(started) == 4, (
        f"the pool started {len(started)} games while the others sat paused; a paused game is not "
        "spending and must not hold the bound"
    )


async def test_a_resting_provider_rests_every_model_it_serves(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue, redis
) -> None:
    """The pairing bug behind three of four paused games. Cooling one model down and pairing its
    neighbour on the same hot pool learns nothing and costs a slot."""
    from chessmark.core.cooldown import ProviderCooldown

    tournament_id, entrants = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )
    # The fixture seeds every model on one provider, so resting it rests the whole field — exactly
    # the shape of a shared free pool, and there is then no game worth starting.
    cooldown = ProviderCooldown(redis)
    await cooldown.note(entrants[0].key, provider="TestProvider", shared_pool=True)

    step = await advance(sessionmaker, queue, tournament_id=tournament_id, cooldown=cooldown)

    assert step.started == 0
    assert not await repo.in_flight(db, tournament_id)


async def test_one_resting_provider_does_not_ground_a_multi_endpoint_model(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue, redis
) -> None:
    """ "Every endpoint", not "any". A paid model served by several providers is not unavailable
    because one of them is resting — the router would simply pick another."""
    from chessmark.core.cooldown import ProviderCooldown

    tournament_id, entrants = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )
    # Give every model a second endpoint elsewhere, then rest only the first provider.
    slugs = [e.key for e in entrants]
    model_ids = list(
        await db.scalars(sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id.in_(slugs)))
    )
    for model_id in model_ids:
        db.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name="Elsewhere",
                supports_tools=True,
                is_active=True,
                uptime_1d=99.0,
            )
        )
    await db.commit()

    cooldown = ProviderCooldown(redis)
    await cooldown.note(slugs[0], provider="TestProvider", shared_pool=True)

    step = await advance(sessionmaker, queue, tournament_id=tournament_id, cooldown=cooldown)

    assert step.started == 1


# ============================================== a fixture that cannot be played (OPS-21)
#
# Seen on the production pool: `gemma-4-26b` v `gemma-4-31b`, both unrated and both served only by
# the same rate-limited Google pool, scheduled seven times over five days and never past ply 0.
# Each game paused until the 24-hour patience ran out, was abandoned — which records no result —
# and was chosen again within the hour, because "least known first" and "prefer an unmet opponent"
# both point straight back at the one pairing that can never inform either.


async def abandon_all_in_flight(db: AsyncSession, tournament_id: uuid.UUID) -> int:
    """Abort every in-flight game the way a provider we cannot reach eventually does."""
    rows = await repo.in_flight(db, tournament_id)
    for row in rows:
        game = await db.get(Game, row.game_id)
        assert game is not None
        game.status = GameStatus.ABORTED
        game.termination = Termination.ABANDONED
        game.termination_detail = "Abandoned after 24.0h and 16 pauses: rate-limited upstream"
    await db.commit()
    return len(rows)


async def test_a_pool_does_not_re_pair_a_fixture_it_abandoned(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """The loop, in the smallest form that reproduces it.

    An abandoned pairing carries no score, so it is absent from `results_so_far` — deliberately,
    because it must never be *scored* (invariant 11). Reading that absence as "these two have never
    met" is the step that was wrong: the rematch penalty never fired, and the same two entrants
    were handed the slot again on the very next tick.
    """
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()
    first = await repo.in_flight(db, tournament_id)
    assert len(first) == 1
    doomed = frozenset({first[0].white_key, first[0].black_key})

    assert await abandon_all_in_flight(db, tournament_id) == 1
    # One tick settles the abandonment, the next chooses what to play instead.
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    rows = await repo.in_flight(db, tournament_id)
    assert len(rows) == 1, "the slot is free again and the pool uses it"
    assert frozenset({rows[0].white_key, rows[0].black_key}) != doomed, (
        "the pairing that produced nothing was scheduled again — seven times, in production"
    )


async def test_an_entrant_that_keeps_producing_nothing_is_rested(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Counting rematches alone would make this worse, not better.

    It stops one fixture repeating; it does not stop a model that cannot play. That model is still
    permanently the least-known entrant, so it takes the slot anyway and now burns a *fresh*
    opponent each time — and both seats of a paused game are parked, so every attempt removes a
    healthy entrant from the pool for as long as the dead one takes to give up.
    """
    tournament_id, _ = await make_tournament(
        db,
        models=6,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    seen: list[frozenset[str]] = []
    for _ in range(DEAD_ATTEMPTS):
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        rows = await repo.in_flight(db, tournament_id)
        assert len(rows) == 1
        seen.append(frozenset({rows[0].white_key, rows[0].black_key}))
        await abandon_all_in_flight(db, tournament_id)

    # Whoever was in both dead pairings has now failed `DEAD_ATTEMPTS` running.
    exhausted = seen[0] & seen[1]
    assert exhausted, "the matchmaker chose the same least-known entrant twice, as it always does"

    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    rows = await repo.in_flight(db, tournament_id)
    assert len(rows) == 1, "the pool plays on — this rests an entrant, it does not withdraw one"
    assert not exhausted & {rows[0].white_key, rows[0].black_key}, (
        "an entrant that has produced nothing twice running was given a third healthy opponent"
    )


async def test_a_rested_entrant_comes_back_by_itself(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
) -> None:
    """Skipped, not withdrawn. A bad afternoon must not remove a model from the benchmark, so the
    rest is measured from the last dead attempt and lapses on its own."""
    tournament_id, _ = await make_tournament(
        db,
        models=4,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
    )

    seen: list[frozenset[str]] = []
    for _ in range(DEAD_ATTEMPTS):
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        rows = await repo.in_flight(db, tournament_id)
        seen.append(frozenset({rows[0].white_key, rows[0].black_key}))
        await abandon_all_in_flight(db, tournament_id)
    exhausted = seen[0] & seen[1]

    # Age every abandonment past the rest, as the clock would.
    await db.execute(
        sa.update(TournamentGame)
        .where(TournamentGame.tournament_id == tournament_id)
        .values(ended_at=dt.datetime.now(dt.UTC) - DEAD_REST - dt.timedelta(minutes=1))
    )
    await db.commit()

    entrants = await repo.entrants_of(db, tournament_id)
    assert await _fruitless_entrants(db, tournament_id, list(entrants)) == set(), (
        f"{exhausted} is still rested after the window lapsed"
    )

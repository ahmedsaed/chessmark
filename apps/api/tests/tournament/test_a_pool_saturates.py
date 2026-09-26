"""A pool with a per-pair target plays each pair that often, then idles for newcomers (ADR-0050).

An engine rating list's gauntlet, in pool form: nobody is played for ever, and a model that joins
later still meets everyone the same number of times.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db import tournaments as repo
from chessmark.db.models import ModelRegistry, Tournament, TournamentGame
from chessmark.orchestration.tournament import advance
from chessmark.tournament import Entrant, FieldFilter, Format, TournamentConfig, matchmake
from tests.tournament.test_runner import (
    abandon_all_in_flight,
    finish_all_in_flight,
    make_tournament,
    seed_models,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_cli = importlib.import_module("tournament")


def _field(*keys: str) -> list[Entrant]:
    return [Entrant(key=key, seed=index) for index, key in enumerate(keys, start=1)]


# ====================================================================== the pure rule


def test_a_pair_at_its_target_is_not_paired_again() -> None:
    games = matchmake(
        _field("a", "b", "c"),
        [],
        {},
        count=3,
        games_per_pair=1,
        pair_games={frozenset(("a", "b")): 1},
    )
    assert games, "c still has two opponents it has not met"
    assert all(frozenset((g.white, g.black)) != frozenset(("a", "b")) for g in games)


def test_a_saturated_field_schedules_nothing() -> None:
    had = {frozenset(("a", "b")): 2, frozenset(("a", "c")): 2, frozenset(("b", "c")): 2}
    assert matchmake(_field("a", "b", "c"), [], {}, count=3, games_per_pair=2, pair_games=had) == []


def test_a_batch_does_not_overshoot_the_target() -> None:
    """Two slots and one pair with one game left: one game, not two."""
    games = matchmake(
        _field("a", "b"), [], {}, count=2, games_per_pair=2, pair_games={frozenset("ab"): 1}
    )
    assert len(games) <= 1


def test_without_a_target_nothing_changes() -> None:
    had = {frozenset(("a", "b")): 50}
    assert matchmake(_field("a", "b"), [], {}, games_per_pair=None, pair_games=had)


def test_the_target_belongs_to_a_pool() -> None:
    with pytest.raises(ValueError, match="pool"):
        TournamentConfig(format=Format.ROUND_ROBIN, games_per_pair=2)
    with pytest.raises(ValueError, match="at least 1"):
        TournamentConfig(format=Format.POOL, games_per_pair=0)


# ====================================================================== through the runner


async def _pool(db: AsyncSession, *, models: int, per_pair: int) -> uuid.UUID:
    tournament_id, _ = await make_tournament(
        db,
        models=models,
        config=TournamentConfig(
            format=Format.POOL, max_concurrent=4, field=FieldFilter(), games_per_pair=per_pair
        ),
    )
    return tournament_id


async def _play_out(db: AsyncSession, sessionmaker: object, queue: object, tid: uuid.UUID) -> int:
    """Tick and settle until the pool starts nothing new. Returns how many games were played."""
    played = 0
    for _ in range(20):
        await advance(sessionmaker, queue, tournament_id=tid)  # type: ignore[arg-type]
        db.expire_all()
        finished = await finish_all_in_flight(db, tid)
        if finished == 0:
            break
        played += finished
    return played


async def _pairs(db: AsyncSession, tid: uuid.UUID) -> dict[frozenset[str], int]:
    tournament = await db.get(Tournament, tid)
    assert tournament is not None
    return await repo.pair_games(db, tid, era=repo.era_of(tournament))


@pytest.mark.integration
async def test_two_entrants_play_their_two_games_and_stop(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: object
) -> None:
    """The Decision Cup's shape: two models, two games — one with each colour — then idle."""
    tid = await _pool(db, models=2, per_pair=2)
    assert await _play_out(db, sessionmaker, queue, tid) == 2

    rows = list(
        await db.scalars(sa.select(TournamentGame).where(TournamentGame.tournament_id == tid))
    )
    assert len(rows) == 2
    assert {row.white_key for row in rows} == {"vendor/model-1", "vendor/model-2"}, (
        "each played White once"
    )
    tournament = await db.get(Tournament, tid)
    assert tournament is not None
    assert await repo.is_saturated(db, tournament, ["vendor/model-1", "vendor/model-2"])


@pytest.mark.integration
async def test_a_newcomer_wakes_the_pool_and_plays_only_its_own_pairs(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: object
) -> None:
    tid = await _pool(db, models=2, per_pair=1)
    assert await _play_out(db, sessionmaker, queue, tid) == 1

    # A third model is listed; the pool re-resolves its field on the next tick and admits it.
    await seed_models(db, 3)
    await db.commit()
    assert await _play_out(db, sessionmaker, queue, tid) == 2

    counts = await _pairs(db, tid)
    assert counts == {
        frozenset(("vendor/model-1", "vendor/model-2")): 1,
        frozenset(("vendor/model-1", "vendor/model-3")): 1,
        frozenset(("vendor/model-2", "vendor/model-3")): 1,
    }, "the old pair was not replayed, and the newcomer met everyone once"


@pytest.mark.integration
async def test_an_abandoned_game_does_not_count_toward_the_target(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: object
) -> None:
    """A target of one game means one game that was decided, not one the harness failed to finish."""
    tid = await _pool(db, models=2, per_pair=1)
    await advance(sessionmaker, queue, tournament_id=tid)
    db.expire_all()
    assert await abandon_all_in_flight(db, tid) == 1

    # Settle the abandonment, then the pair is owed its game.
    await advance(sessionmaker, queue, tournament_id=tid)
    db.expire_all()
    await advance(sessionmaker, queue, tournament_id=tid)
    db.expire_all()
    assert len(await repo.in_flight(db, tid)) == 1


# ====================================================================== the operator's command


@pytest.mark.integration
async def test_a_running_pool_can_be_given_a_target_and_have_it_cleared(db: AsyncSession) -> None:
    """How `pool-free` gets one: `set pool-free --games-per-pair 2`, and `0` to undo it."""
    await _pool(db, models=2, per_pair=1)
    await db.commit()
    slug = await db.scalar(sa.select(Tournament.slug))
    assert slug is not None

    assert await _cli.apply_games_per_pair(db, slug=slug, value=3) == (1, 3)
    assert await _cli.apply_games_per_pair(db, slug=slug, value=0) == (3, None)


@pytest.mark.integration
async def test_a_closed_event_refuses_a_target(db: AsyncSession) -> None:
    tid, _ = await make_tournament(
        db, models=2, config=TournamentConfig(format=Format.ROUND_ROBIN, field=FieldFilter())
    )
    tournament = await db.get(Tournament, tid)
    assert tournament is not None
    with pytest.raises(ValueError, match="only a pool"):
        await _cli.apply_games_per_pair(db, slug=tournament.slug, value=2)


@pytest.mark.integration
async def test_a_model_that_leaves_is_not_waited_for_and_is_owed_its_pairs_on_return(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: object
) -> None:
    """No operator step either way: the pool re-resolves its field every tick (ADR-0050)."""
    tid = await _pool(db, models=3, per_pair=1)
    gone = "vendor/model-3"
    await db.execute(
        sa.update(ModelRegistry).where(ModelRegistry.openrouter_id == gone).values(enabled=False)
    )
    await db.commit()

    # Only the pair still in the field is played, and the pool counts itself saturated without
    # waiting on two pairs that can never start.
    assert await _play_out(db, sessionmaker, queue, tid) == 1
    tournament = await db.get(Tournament, tid)
    assert tournament is not None
    field = list(await repo.in_field(db, tournament, FieldFilter()))
    assert gone not in field
    assert await repo.is_saturated(db, tournament, field)

    # Listed again: it is owed exactly its two pairs, and the met pair is not replayed.
    await db.execute(
        sa.update(ModelRegistry).where(ModelRegistry.openrouter_id == gone).values(enabled=True)
    )
    await db.commit()
    assert await _play_out(db, sessionmaker, queue, tid) == 2
    assert set((await _pairs(db, tid)).values()) == {1}

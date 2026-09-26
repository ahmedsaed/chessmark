"""A decision-model event runs through the same runner as any other (ADR-0049)."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db import tournaments as repo
from chessmark.db.enums import ModelRuntime
from chessmark.db.models import Game, ModelEndpoint, ModelRegistry, Player, Tournament
from chessmark.orchestration.tournament import advance
from chessmark.tournament import FieldFilter, Format, TournamentConfig
from tests.tournament.test_runner import seed_models

pytestmark = pytest.mark.integration


async def _decision_models(db: AsyncSession, count: int) -> list[str]:
    slugs = []
    for index in range(count):
        row = ModelRegistry(
            openrouter_id=f"decider/d{index}",
            display_name=f"Decider {index}",
            provider="decider",
            supports_tools=False,
            context_length=8_192,
            runtime=ModelRuntime.DECISION,
        )
        db.add(row)
        await db.flush()
        db.add(ModelEndpoint(model_id=row.id, provider_name="Host", supports_tools=False))
        slugs.append(row.openrouter_id)
    await db.flush()
    return slugs


async def test_a_decision_pool_seats_only_decision_models_every_tick(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: object
) -> None:
    await seed_models(db, 3)
    decision = await _decision_models(db, 3)

    config = TournamentConfig(
        format=Format.POOL, max_concurrent=2, field=FieldFilter(runtime="decision")
    )
    entrants = await repo.resolve_field(db, config.field)
    tournament = await repo.create_tournament(
        db, name="Deciders", slug=f"d-{uuid.uuid4().hex[:8]}", config=config, entrants=entrants
    )
    tournament_id = tournament.id
    await db.commit()

    # A pool re-resolves its field on every tick, so the stored filter has to say what it is.
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    seated = {e.key for e in await repo.entrants_of(db, tournament_id)}
    assert seated == set(decision)

    rows = await repo.in_flight(db, tournament_id)
    assert rows
    stored = await db.get(Tournament, tournament_id)
    assert stored is not None
    assert all(row.era == repo.era_of(stored) for row in rows)

    games = [row.game_id for row in rows]
    runtimes = set(await db.scalars(sa.select(Player.runtime).where(Player.game_id.in_(games))))
    assert runtimes == {ModelRuntime.DECISION}
    versions = set(await db.scalars(sa.select(Game.prompt_version).where(Game.id.in_(games))))
    assert versions == {None}

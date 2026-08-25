"""The playable-model registry (UI-07)."""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.api.deps import SessionDep
from chessmark.api.schemas import LeaderboardRow, ModelDetail, ModelOut, ModelStatsOut
from chessmark.bench.service import compute_aggregates, compute_ratings
from chessmark.db.models import ModelEndpoint, ModelRegistry
from chessmark.db.stats import model_stats

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
async def list_models(
    session: SessionDep,
    free_only: Annotated[bool, Query()] = False,
) -> list[ModelOut]:
    """Models a game may actually use.

    Filtered to tool-capable models: an agent acts only through tools (AGENT-01), so one without
    them cannot play at all and listing it would only invite a confusing 400 later.
    """
    query = sa.select(ModelRegistry).where(
        ModelRegistry.enabled.is_(True), ModelRegistry.supports_tools.is_(True)
    )
    if free_only:
        query = query.where(ModelRegistry.is_free.is_(True))

    rows = list(await session.scalars(query.order_by(ModelRegistry.openrouter_id)))
    if not rows:
        return []

    endpoints = list(
        await session.scalars(
            sa.select(ModelEndpoint).where(
                ModelEndpoint.model_id.in_([row.id for row in rows]),
                ModelEndpoint.is_active.is_(True),
            )
        )
    )
    by_model: dict[uuid.UUID, list[ModelEndpoint]] = {}
    for endpoint in endpoints:
        by_model.setdefault(endpoint.model_id, []).append(endpoint)

    return [ModelOut.from_model(row, endpoints=by_model.get(row.id, [])) for row in rows]


@router.get("/{slug:path}", response_model=ModelDetail)
async def get_model(session: SessionDep, slug: str) -> ModelDetail:
    """One model, with what it has actually done (Phase 20).

    `{slug:path}` because an OpenRouter id contains a slash — `google/gemini-3.7-flash` is one
    identifier, not a nested route, and the default converter would refuse it.

    The aggregates cover **every** game, not only the ratable ones the leaderboard counts. A model
    that has only ever played exhibition games has done things worth reporting, and a page that
    showed nothing for it would be describing the rating rules rather than the model.
    """
    row = await session.scalar(sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == slug))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No model {slug!r} in the registry."
        )

    endpoints = list(
        await session.scalars(sa.select(ModelEndpoint).where(ModelEndpoint.model_id == row.id))
    )
    base = ModelOut.from_model(row, endpoints=endpoints)
    stats = await model_stats(session, row)

    # Ratings, where this model's contestants hold any. Recomputed rather than cached for the same
    # reason the leaderboard is: a rating is a pure function of the games behind it.
    run = await compute_ratings(session, prompt_version=PROMPT_VERSION)
    aggregates = await compute_aggregates(session, prompt_version=PROMPT_VERSION)
    ratings = [
        LeaderboardRow.from_rating(
            contestant, rating, aggregates.get(contestant), display_name=row.display_name
        )
        for contestant, rating in run.ratings.items()
        if contestant.model_id == row.id
    ]

    return ModelDetail(**base.model_dump(), stats=ModelStatsOut.from_stats(stats), ratings=ratings)

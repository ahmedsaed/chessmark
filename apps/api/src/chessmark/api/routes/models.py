"""The playable-model registry (UI-07)."""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.agents.registry import endpoint_is_playable
from chessmark.api.deps import SessionDep
from chessmark.api.schemas import (
    ExcludedGame,
    LeaderboardRow,
    ModelDetail,
    ModelOut,
    ModelStatsOut,
)
from chessmark.bench import snapshot
from chessmark.db.models import ModelEndpoint, ModelRegistry, Player
from chessmark.db.stats import model_stats

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
async def list_models(
    session: SessionDep,
    free_only: Annotated[bool, Query()] = False,
    playable: Annotated[
        bool, Query(description="Only models with an active tool-capable endpoint")
    ] = True,
) -> list[ModelOut]:
    """Models a game may actually use.

    **Playable by default**, meaning the model has at least one active tool-capable endpoint. A
    registered model with none has no contestants and cannot be picked (ADR-0015) — it is a record
    that its providers dropped it, not an offer. Listing those was a real bug: the picker filtered
    them and the catalogue page did not, so `/models` advertised 18 models nobody could play.

    Pass `playable=false` for the registry as stored, which is what an operator auditing what
    disappeared upstream wants.
    """
    query = sa.select(ModelRegistry).where(
        ModelRegistry.enabled.is_(True), ModelRegistry.supports_tools.is_(True)
    )
    if free_only:
        query = query.where(ModelRegistry.is_free.is_(True))
    if playable:
        # The same predicate `select_endpoint` pins by, so the catalogue cannot advertise a model
        # the picker would refuse — including one whose only endpoint's window is under the floor.
        query = query.where(
            ModelRegistry.id.in_(sa.select(ModelEndpoint.model_id).where(*endpoint_is_playable()))
        )

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

    **The whole of what is known about one model**, because this is now the only page about it —
    the leaderboard's per-contestant drill-down redirects here. Three scopes, kept apart on purpose
    rather than blended into one number:

    * `stats` covers **every** game — exhibition, human, ranked alike. A model that has only ever
      played exhibitions has done things worth reporting, and a page that showed nothing for it
      would be describing the rating rules rather than the model.
    * `ratings` and `rated_games` cover the **ratable** games, per contestant. A contestant is
      `(model, precision)` (ADR-0015), so a model served at two precisions holds two ratings and
      each reaches its own games (BENCH-02).
    * `excluded` is the difference, with the reason per game (BENCH-10). It is the answer to "why
      does the record say fifteen and the rating say nine", which is the question two pages showing
      two W/D/L figures used to raise and neither could answer.
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

    # Read from the stored run, never recomputed here (ADR-0032). This called `compute_ratings` and
    # `compute_aggregates` without sharing a scan, so one model page was **two** full sweeps of the
    # archive — the precise cost the snapshot exists to remove, reintroduced on a different route
    # because nothing measured this one. `test_a_model_page_costs_a_fixed_number_of_queries` does.
    stored = await snapshot.current(session, prompt_version=PROMPT_VERSION)
    ratings = [
        LeaderboardRow(**entry, display_name=row.display_name)
        for entry in stored["rows"]
        if str(entry["model_id"]) == str(row.id)
    ]

    # The games behind each rating, keyed the way the run stored them. Carried as ids rather than
    # summaries because the page already holds this model's games: it partitions the list it has
    # instead of fetching the same rows a second time under another name.
    labels = {f"{rating.model_slug}@{rating.quantization}" for rating in ratings}
    rated_games = {
        label: [uuid.UUID(game_id) for game_id in game_ids]
        for label, game_ids in stored["games_by_contestant"].items()
        if label in labels
    }

    # ...and the finished games that did not count. The snapshot's exclusions cover every game, so
    # they are narrowed to this model's seats here — one indexed read, not a scan.
    seated = set(await session.scalars(sa.select(Player.game_id).where(Player.model_id == row.id)))
    excluded = [
        ExcludedGame(game_id=uuid.UUID(entry["game_id"]), reason=entry["reason"])
        for entry in stored["excluded"]
        if uuid.UUID(entry["game_id"]) in seated
    ]

    return ModelDetail(
        **base.model_dump(),
        stats=ModelStatsOut.from_stats(stats),
        ratings=ratings,
        rated_games=rated_games,
        excluded=excluded,
    )

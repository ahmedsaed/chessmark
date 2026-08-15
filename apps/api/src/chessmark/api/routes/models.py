"""The playable-model registry (UI-07)."""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query

from chessmark.api.deps import SessionDep
from chessmark.api.schemas import ModelOut
from chessmark.db.models import ModelEndpoint, ModelRegistry

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

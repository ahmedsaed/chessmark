"""The playable-model registry (UI-07)."""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query

from chessmark.api.deps import SessionDep
from chessmark.api.schemas import ModelOut
from chessmark.db.models import ModelRegistry

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

    rows = await session.scalars(query.order_by(ModelRegistry.openrouter_id))
    return [ModelOut.from_model(row) for row in rows]

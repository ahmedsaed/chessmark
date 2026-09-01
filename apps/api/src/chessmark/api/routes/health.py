"""Liveness and readiness probes (OPS-06)."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Response, status

from chessmark import __version__
from chessmark.api.deps import RedisDep, SessionDep
from chessmark.api.schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])

#: Read from the installed distribution, never written here — see `chessmark/__init__.py`.
VERSION = __version__


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness. Deliberately touches no dependency — it answers "is this process alive", and a
    probe that fails when the database blips would restart a perfectly healthy container."""
    return HealthResponse(status="ok", version=VERSION)


@router.get("/ready", response_model=ReadinessResponse)
async def ready(session: SessionDep, redis: RedisDep, response: Response) -> ReadinessResponse:
    """Readiness: can this process actually serve traffic?

    Returns 503 when a dependency is down so a load balancer stops sending requests here, without
    the process being killed.
    """
    database = True
    try:
        await session.execute(sa.text("SELECT 1"))
    except Exception:
        database = False

    cache = True
    try:
        await redis.ping()
    except Exception:
        cache = False

    healthy = database and cache
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if healthy else "degraded", database=database, redis=cache
    )

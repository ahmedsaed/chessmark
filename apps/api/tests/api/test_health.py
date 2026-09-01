"""Liveness and readiness (OPS-06)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from chessmark import __version__
from chessmark.api.deps import get_redis, get_session
from chessmark.main import create_app

pytestmark = pytest.mark.integration


async def test_liveness_needs_no_dependency() -> None:
    """A probe that fails when the database blips would restart a healthy container."""
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_the_reported_version_is_the_one_that_shipped() -> None:
    """The assertion that used to be `== "0.1.0"`, and could not catch what it was for.

    A literal has to be edited on every bump, so the failure it produces always reads as "the test
    is stale" — never as "the API is reporting a version it is not". Meanwhile `0.1.0` was written
    in three independently-editable places and nothing tied them: bump `pyproject.toml` and
    `/health` keeps serving the old number while `/openapi.json` disagrees with both.

    Comparing against the installed distribution is the whole point. It never needs editing, and it
    fails exactly when a copy has drifted back in.
    """
    from importlib.metadata import version as distribution_version

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        spec = await client.get("/openapi.json")

    packaged = distribution_version("chessmark-api")
    assert health.json()["version"] == packaged
    assert spec.json()["info"]["version"] == packaged, (
        "the OpenAPI metadata is a third copy, and it is the frontend's contract"
    )


async def test_readiness_reports_both_dependencies(client: AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": True, "redis": True}


async def test_readiness_is_503_when_a_dependency_is_down() -> None:
    """Load balancers stop routing here without the process being killed."""

    class Broken:
        async def execute(self, *_args: object, **_kwargs: object) -> object:
            msg = "database is gone"
            raise RuntimeError(msg)

        async def ping(self) -> bool:
            msg = "redis is gone"
            raise RuntimeError(msg)

    app: FastAPI = create_app()
    app.dependency_overrides[get_session] = lambda: Broken()
    app.dependency_overrides[get_redis] = lambda: Broken()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body == {"status": "degraded", "database": False, "redis": False}


async def test_openapi_is_valid(client: AsyncClient) -> None:
    """The schema is the frontend's contract, so a broken one is a broken build."""
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "Chessmark API"

    paths = set(spec["paths"])
    assert {"/games", "/games/{game_id}", "/games/{game_id}/stream", "/models"} <= paths
    assert spec["paths"]["/games"]["post"]["responses"]["201"]

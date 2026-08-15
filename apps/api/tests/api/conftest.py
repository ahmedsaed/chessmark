"""Fixtures for API tests.

The app runs against the real test database and real Redis, with only the provider scripted. The
dependency overrides exist so the app uses the *same* session and Redis the test does — otherwise
the test would write to one connection and the API read from another.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.api.deps import get_redis, get_session
from chessmark.main import create_app


@pytest.fixture
def app(db: AsyncSession, sessionmaker: Any, redis: Any) -> FastAPI:
    application = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        # A fresh session per request, not the test's own. Sharing one looks convenient but the
        # worker commits through a *different* session, so the test's identity map would hold
        # stale rows and the API would answer with pre-turn state. A per-request session is also
        # what production does.
        async with sessionmaker() as session:
            yield session

    async def _redis() -> Any:
        return redis

    application.dependency_overrides[get_session] = _session
    application.dependency_overrides[get_redis] = _redis
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def parse_sse(text: str) -> list[dict[str, str]]:
    """Parse an SSE body into frames.

    Hand-rolled rather than using a library: the point of these tests is that we emit a correct
    wire format, and a parser that shares assumptions with the producer would hide a mistake.
    """
    frames: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            if current:
                frames.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        current[field.strip()] = value.lstrip()

    if current:
        frames.append(current)
    return frames

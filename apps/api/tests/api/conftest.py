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

from chessmark.api.deps import get_redis, get_session, get_verifier
from chessmark.core.auth import AuthError, Principal, TokenVerifier
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

    # No Clerk, ever. The verifier is replaced with one that accepts exactly the tokens this suite
    # mints — the same reasoning as the scripted provider: exercise the real dependency graph with
    # only the third party swapped out.
    application.dependency_overrides[get_verifier] = lambda: FakeVerifier()
    return application


VALID_TOKENS: dict[str, Principal] = {}


class FakeVerifier(TokenVerifier):
    """Accepts tokens registered by `as_user`, rejects everything else.

    Deliberately *not* a blanket allow. Half the tests here are about what happens to a caller
    without a token or with a bad one, and a verifier that waved everything through would make
    those tests pass without testing anything.
    """

    def __init__(self) -> None:
        super().__init__(jwks_url="https://clerk.test/.well-known/jwks.json")

    def verify(self, token: str) -> Principal:
        principal = VALID_TOKENS.get(token)
        if principal is None:
            raise AuthError("unknown test token")
        return principal


def as_user(
    clerk_user_id: str = "user_test", *, email: str | None = "test@chessmark.test"
) -> dict[str, str]:
    """Mint a token for this suite and return the header that carries it."""
    token = f"token-for-{clerk_user_id}"
    VALID_TOKENS[token] = Principal(
        clerk_user_id=clerk_user_id, email=email, display_name=clerk_user_id
    )
    return {"authorization": f"Bearer {token}"}


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

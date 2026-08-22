"""Request dependencies.

Each is a thin seam so tests can override it — the API tier holds no state of its own, which is
what lets it scale horizontally (ADR-0007).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Header, HTTPException, Path, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.auth import (
    AuthError,
    ConfigurationError,
    Principal,
    TokenVerifier,
    bearer_token,
)
from chessmark.core.budget import GlobalBudget
from chessmark.core.config import Settings, get_settings
from chessmark.core.ratelimit import RateLimiter
from chessmark.db.models import Game, User
from chessmark.db.repositories import GameNotFoundError, get_game
from chessmark.db.session import get_sessionmaker
from chessmark.db.users import user_for
from chessmark.orchestration.queue import TurnQueue

if TYPE_CHECKING:
    # redis-py's `Redis` is generic to type checkers but not at runtime, and FastAPI evaluates
    # dependency annotations with `eval_str=True` — so a subscripted `Redis[Any]` in a signature
    # raises "is not a generic class" at import. Subscript for mypy, bare for FastAPI.
    RedisClient = Redis[Any]
else:
    RedisClient = Redis

_redis: RedisClient | None = None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def get_redis() -> RedisClient:
    """A shared Redis client.

    One connection pool per process: SSE holds a subscription open for the life of a request, so
    a client per request would exhaust connections under a few hundred spectators (NFR-04).
    """
    global _redis
    if _redis is None:
        _redis = Redis.from_url(str(get_settings().redis_url))
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()  # type: ignore[attr-defined]
        _redis = None


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[RedisClient, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_queue(redis: RedisDep) -> TurnQueue:
    queue = TurnQueue(redis)
    await queue.ensure_group()
    return queue


QueueDep = Annotated[TurnQueue, Depends(get_queue)]


async def load_game(
    session: SessionDep,
    game_id: Annotated[uuid.UUID, Path(description="Game id")],
) -> Game:
    try:
        return await get_game(session, game_id)
    except GameNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No game with id {game_id}"
        ) from error


GameDep = Annotated[Game, Depends(load_game)]


# ---------------------------------------------------------------------- auth

_verifier: TokenVerifier | None = None


def get_verifier() -> TokenVerifier:
    """One verifier per process; it holds the JWKS cache.

    A per-request instance would refetch Clerk's keys on every call, which is both slow and a good
    way to get rate-limited by our own identity provider.
    """
    global _verifier
    if _verifier is None:
        settings = get_settings()
        _verifier = TokenVerifier(
            jwks_url=settings.clerk_jwks_url,
            issuer=settings.clerk_issuer or None,
        )
    return _verifier


def reset_verifier() -> None:
    """Drop the cached verifier. For tests and for config reloads."""
    global _verifier
    _verifier = None


VerifierDep = Annotated[TokenVerifier, Depends(get_verifier)]


async def get_principal(
    verifier: VerifierDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """The verified caller, or 401.

    `WWW-Authenticate` is set because RFC 6750 requires it on a 401 from a bearer-token resource,
    and because it is what tells a client this is an auth problem rather than a permissions one.
    """
    try:
        return verifier.verify(bearer_token(authorization))
    except ConfigurationError as error:
        # Our misconfiguration, not the caller's problem. A 401 here would send every user to log
        # in again to fix something only we can fix.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is not configured on this server.",
        ) from error
    except AuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_current_user(session: SessionDep, principal: PrincipalDep) -> User:
    """The `users` row for the caller, created on first sight (see `db/users.py`)."""
    return await user_for(session, principal)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(user: CurrentUser) -> User:
    """An admin, or 403.

    403 rather than 404: hiding the existence of an admin surface behind a 404 buys nothing here —
    the routes are in the public OpenAPI schema — and a 403 tells an operator who forgot to set
    their own flag what actually happened.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required."
        )
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


# ---------------------------------------------------------------------- spend controls


async def get_budget(redis: RedisDep, settings: SettingsDep) -> GlobalBudget:
    return GlobalBudget(redis, daily_limit_usd=Decimal(str(settings.global_daily_usd_budget)))


BudgetDep = Annotated[GlobalBudget, Depends(get_budget)]


async def get_rate_limiter(redis: RedisDep, settings: SettingsDep) -> RateLimiter:
    return RateLimiter(
        redis,
        limit=settings.rate_limit_per_window,
        window_seconds=settings.rate_limit_window_seconds,
    )


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def enforce_rate_limit(
    request: Request,
    limiter: RateLimiterDep,
    user: CurrentUser,
) -> None:
    """Refuse a caller who is asking too fast (AUTH-06).

    Keyed on our own `users.id` rather than on an IP: the limit is about who is spending the
    budget, and an IP is both shared by legitimate users behind a NAT and trivially changed by
    anyone who cares to.
    """
    decision = await limiter.check(str(user.id), action=request.url.path)
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests. Try again in {decision.retry_after}s.",
            headers={"retry-after": str(decision.retry_after)},
        )

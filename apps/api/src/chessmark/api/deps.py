"""Request dependencies.

Each is a thin seam so tests can override it — the API tier holds no state of its own, which is
what lets it scale horizontally (ADR-0007).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Path, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.config import Settings, get_settings
from chessmark.db.models import Game
from chessmark.db.repositories import GameNotFoundError, get_game
from chessmark.db.session import get_sessionmaker
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

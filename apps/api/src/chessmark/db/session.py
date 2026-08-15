"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from chessmark.core.config import get_settings


def build_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return build_engine(settings.database_url, echo=settings.debug and False)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional session that commits on success and rolls back on failure.

    Used by workers. NFR-08 says a partial outage must never corrupt a game record, which means a
    ply and everything it produced — its turn, LLM calls, tool calls, and cost rollup — commit
    together or not at all.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Read paths mostly; writers should use `session_scope`."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    """Close the pool on shutdown, and reset the cache so tests can rebind."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

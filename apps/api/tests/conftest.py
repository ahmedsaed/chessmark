"""Shared test fixtures.

Database tests run against a real PostgreSQL — the schema relies on JSONB, row-level locking, and
`UPDATE ... RETURNING`, none of which a mock or SQLite would exercise honestly.

The schema is built by running Alembic rather than `create_all`, so every test run also proves the
migrations produce a usable database.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from chessmark.db import models  # noqa: F401  registers the tables on Base.metadata
from chessmark.db.base import Base

API_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEST_URL = "postgresql+asyncpg://chessmark:chessmark@localhost:5433/chessmark_test"


def _resolve_test_database_url() -> str:
    """Pick the test database, and refuse to touch anything that isn't obviously one.

    These tests TRUNCATE every table between cases. Running that against a development database
    by accident is the kind of mistake you only make once, so the name check is not negotiable.
    """
    url = os.environ.get("TEST_DATABASE_URL")

    if not url:
        base = os.environ.get("DATABASE_URL", DEFAULT_TEST_URL)
        parts = urlsplit(base)
        name = parts.path.lstrip("/")
        if not name.endswith("_test"):
            name = f"{name}_test"
        url = urlunsplit(parts._replace(path=f"/{name}"))

    database = urlsplit(url).path.lstrip("/")
    if "test" not in database:
        msg = (
            f"refusing to run destructive tests against database {database!r} — "
            "its name must contain 'test'"
        )
        raise RuntimeError(msg)

    return url


async def _ensure_database_exists(url: str) -> None:
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    admin_url = urlunsplit(parts._replace(path="/postgres"))

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": database}
            )
            if not exists:
                await conn.execute(sa.text(f'CREATE DATABASE "{database}"'))
    finally:
        await engine.dispose()


async def _drop_schema(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # One statement per execute: asyncpg prepares every statement, and a prepared
            # statement cannot contain multiple commands.
            await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    """A migrated, empty test database.

    Synchronous on purpose: Alembic's async env runs its own event loop, so this must not be
    called from inside one.
    """
    url = _resolve_test_database_url()

    # Skip only when the database genuinely isn't there. Anything else — a bad statement, a
    # broken migration — must fail loudly; a blanket except here would turn real bugs into a
    # wall of green skips.
    try:
        asyncio.run(_ensure_database_exists(url))
    except (OSError, sa.exc.InterfaceError) as exc:  # pragma: no cover - environment problem
        pytest.skip(f"PostgreSQL is not reachable at {urlsplit(url).netloc}: {exc}")

    asyncio.run(_drop_schema(url))

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    os.environ["ALEMBIC_DATABASE_URL"] = url
    command.upgrade(config, "head")

    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    os.environ["ALEMBIC_DATABASE_URL"] = database_url
    return config


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """A fresh engine per test.

    Per-test rather than per-session because pytest-asyncio gives each test its own event loop,
    and an engine bound to a closed loop fails in ways that are tedious to debug.
    """
    created = create_async_engine(database_url, pool_size=25, max_overflow=10)
    try:
        yield created
    finally:
        await created.dispose()


async def _truncate_all(engine: AsyncEngine) -> None:
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(
    engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncSession]:
    """A clean database and an open session. Commits are real."""
    await _truncate_all(engine)
    async with sessionmaker() as session:
        yield session


@pytest.fixture(autouse=True)
def _mark_database_tests(request: pytest.FixtureRequest) -> Iterator[None]:
    """Anything using a database fixture is an integration test, whether it said so or not."""
    if {"db", "engine", "sessionmaker", "database_url"} & set(request.fixturenames):
        request.node.add_marker(pytest.mark.integration)
    yield


# ---------------------------------------------------------------------- redis


DEFAULT_TEST_REDIS = "redis://localhost:6380/15"


def _resolve_test_redis_url() -> str:
    """A dedicated Redis database for tests.

    These fixtures FLUSHDB between cases, so the index must not be the one the app uses. Index 15
    by convention; the app defaults to 0.
    """
    url = os.environ.get("TEST_REDIS_URL")
    if url:
        return url

    base = os.environ.get("REDIS_URL", DEFAULT_TEST_REDIS)
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path="/15"))


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    """A flushed Redis database."""
    from redis.asyncio import Redis

    client: Any = Redis.from_url(_resolve_test_redis_url())
    try:
        try:
            await client.ping()
        except Exception as exc:  # pragma: no cover - environment problem
            pytest.skip(f"Redis is not reachable at {_resolve_test_redis_url()}: {exc}")
        await client.flushdb()
        yield client
    finally:
        await client.aclose()

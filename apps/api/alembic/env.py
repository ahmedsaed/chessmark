"""Alembic environment, wired for async SQLAlchemy.

The database URL comes from `Settings` (overridable with `ALEMBIC_DATABASE_URL`, which the test
suite uses to point at a scratch database), so it is never duplicated in `alembic.ini`.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from chessmark.core.config import get_settings
from chessmark.db.base import Base

# Importing the models registers every table on Base.metadata. Without this, autogenerate and
# `alembic check` would both cheerfully report an empty schema.
from chessmark.db import models  # noqa: F401  isort:skip

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

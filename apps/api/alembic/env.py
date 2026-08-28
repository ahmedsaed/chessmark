"""Alembic environment, wired for async SQLAlchemy.

The database URL comes from `Settings` (overridable with `ALEMBIC_DATABASE_URL`, which the test
suite uses to point at a scratch database), so it is never duplicated in `alembic.ini`.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

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


#: How long a migration will wait for a lock before giving up. Seconds; `0` disables the bound.
#:
#: **Not a performance setting — a diagnostic one.** `lock_timeout` bounds only the *wait* for a
#: lock, never the work, so a slow backfill is unaffected and a blocked `ALTER TABLE` fails in
#: seconds instead of hanging.
#:
#: A deploy hung on `ALTER TABLE games ALTER COLUMN ... SET DEFAULT`, which is a catalogue-only
#: change taking microseconds — but it needs ACCESS EXCLUSIVE, and that conflicts with the ACCESS
#: SHARE every plain `SELECT` on the table holds. The worker keeps **one transaction open for a
#: whole turn** (NFR-08), and a free model's turn runs to 442 seconds, so the migration queued
#: behind it. Worse: a queued ACCESS EXCLUSIVE sits at the head of the lock queue and blocks
#: everything behind *it*, so the API stalled too and the whole thing read as a hang rather than a
#: wait.
#:
#: Failing fast is strictly better. "Could not get the lock" names the problem; a silent wait does
#: not, and the operator's next move — find who holds it — is the same either way.
LOCK_TIMEOUT_SECONDS = int(os.environ.get("ALEMBIC_LOCK_TIMEOUT_SECONDS", "10"))


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

    # **Set on the connection, not with a statement inside the migration.** Issuing
    # `SET lock_timeout` from `do_run_migrations` starts an implicit transaction before Alembic
    # opens its own, and the whole upgrade then rolls back silently — migrations "ran", every table
    # was missing, and the only symptom was a later test saying `relation "users" does not exist`.
    # asyncpg applies `server_settings` at connect time, outside any transaction.
    connect_args: dict[str, Any] = {}
    if LOCK_TIMEOUT_SECONDS > 0:
        connect_args["server_settings"] = {"lock_timeout": f"{LOCK_TIMEOUT_SECONDS}s"}

    engine = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

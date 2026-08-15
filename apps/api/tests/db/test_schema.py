"""Structural guarantees about the schema itself.

Postgres does not index foreign keys for you. A benchmark whose whole job is joining plies, turns,
and LLM calls back to a game would feel every missing one, and the omission is invisible until the
tables are large — so it is asserted rather than remembered.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from chessmark.db import models  # noqa: F401  registers the tables
from chessmark.db.base import Base

FOREIGN_KEY_COLUMNS = sa.text("""
    SELECT c.conrelid::regclass::text AS table_name,
           a.attname                  AS column_name
    FROM pg_constraint c
    JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
    WHERE c.contype = 'f'
      AND c.connamespace = 'public'::regnamespace
      AND k.ord = 1
""")

INDEX_LEADING_COLUMNS = sa.text("""
    SELECT i.indrelid::regclass::text AS table_name,
           a.attname                  AS column_name
    FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
    JOIN pg_class t ON t.oid = i.indrelid
    WHERE t.relnamespace = 'public'::regnamespace
""")


@pytest.mark.integration
async def test_every_foreign_key_has_a_supporting_index(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        foreign_keys = {tuple(row) for row in (await conn.execute(FOREIGN_KEY_COLUMNS)).all()}
        indexed = {tuple(row) for row in (await conn.execute(INDEX_LEADING_COLUMNS)).all()}

    assert foreign_keys, "introspection found no foreign keys — the query is wrong"

    unindexed = sorted(foreign_keys - indexed)
    assert not unindexed, (
        "these foreign keys have no index with them as the leading column: "
        f"{unindexed}. Add index=True to the column."
    )


@pytest.mark.integration
async def test_every_model_table_exists(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        present = {row[0] for row in rows.all()}

    missing = sorted(set(Base.metadata.tables) - present)
    assert not missing, f"migration did not create: {missing}"


@pytest.mark.integration
async def test_public_identifiers_are_uuids(engine: AsyncEngine) -> None:
    """Game URLs are shareable, so they must not be enumerable."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'id' "
                "AND table_name IN ('games', 'users', 'players', 'model_registry')"
            )
        )
        types = dict(rows.all())

    assert types == {
        "games": "uuid",
        "users": "uuid",
        "players": "uuid",
        "model_registry": "uuid",
    }


@pytest.mark.integration
async def test_verbatim_payload_columns_are_jsonb(engine: AsyncEngine) -> None:
    """LOG-01 stores real payloads; JSONB keeps them queryable rather than opaque text."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name || '.' || column_name, data_type "
                "FROM information_schema.columns WHERE table_schema = 'public' "
                "AND (table_name, column_name) IN "
                "(('llm_calls','request'), ('llm_calls','response'), "
                " ('tool_calls','arguments'), ('tool_calls','result'), "
                " ('game_events','payload'))"
            )
        )
        types = dict(rows.all())

    assert set(types.values()) == {"jsonb"}, types


# ------------------------------------------------------------------ migrations


@pytest.mark.integration
def test_migrations_report_no_drift(alembic_config: Config) -> None:
    """`alembic check` fails if the models and the migrations disagree (OPS-02)."""
    command.check(alembic_config)


@pytest.mark.integration
def test_migrations_downgrade_and_upgrade_cleanly(alembic_config: Config) -> None:
    """A migration that cannot be reversed is a migration that cannot be rolled back."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)

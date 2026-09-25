"""the archive can search a player's name

Revision ID: 7a3c1e9b2f40
Revises: 06d9450ac683
Create Date: 2026-09-25 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7a3c1e9b2f40"
down_revision: str | None = "06d9450ac683"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `pg_trgm` ships in contrib with the official image and is a *trusted* extension since
    # Postgres 13, so the database owner can create it without a superuser. `IF NOT EXISTS`
    # because the extension is database-wide and outlives this table.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_players_display_name_trgm",
        "players",
        ["display_name"],
        postgresql_using="gin",
        postgresql_ops={"display_name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # The extension is left in place: something added after this revision may rely on it, and an
    # unused extension costs nothing.
    op.drop_index("ix_players_display_name_trgm", table_name="players")

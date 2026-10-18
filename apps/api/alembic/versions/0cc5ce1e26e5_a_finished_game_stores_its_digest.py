"""a finished game stores its digest

Revision ID: 0cc5ce1e26e5
Revises: 8ddbdc635007
Create Date: 2026-10-18 16:05:18.611106
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0cc5ce1e26e5"
down_revision: str | None = "8ddbdc635007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and unfilled: a digest is computed from the game's record on its first read, and
    # `make backfill-digests` does the archive ahead of time (ADR-0069). Filling it here would put
    # chess and the opening table inside a migration.
    op.add_column(
        "games",
        sa.Column("digest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("games", "digest")

"""reasoning levels on models and endpoints

Revision ID: 8ddbdc635007
Revises: 9a854789833f
Create Date: 2026-10-09 16:50:19.369832
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8ddbdc635007"
down_revision: str | None = "9a854789833f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # All nullable and unfilled: the levels come from OpenRouter, which a migration cannot ask. Until
    # the catalogue refresh that runs at deploy fills them, a model sends no level and records none,
    # and that same refresh labels those seats afterwards (ADR-0067).
    op.add_column("model_endpoints", sa.Column("supports_reasoning", sa.Boolean(), nullable=True))
    op.add_column(
        "model_registry",
        sa.Column("reasoning", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "model_registry", sa.Column("reasoning_levels", sa.ARRAY(sa.Text()), nullable=True)
    )
    op.add_column("model_registry", sa.Column("default_reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_registry", "default_reasoning")
    op.drop_column("model_registry", "reasoning_levels")
    op.drop_column("model_registry", "reasoning")
    op.drop_column("model_endpoints", "supports_reasoning")

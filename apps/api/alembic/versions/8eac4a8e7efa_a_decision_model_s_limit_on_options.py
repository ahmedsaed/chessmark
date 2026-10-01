"""a decision model's limit on options per question (ADR-0059)

Revision ID: 8eac4a8e7efa
Revises: 9b1d4c7e2a10
Create Date: 2026-10-01 14:41:38.435756
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8eac4a8e7efa"
down_revision: str | None = "9b1d4c7e2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and unset: every decision model is re-checked under `d2.1` at the next catalogue
    # refresh, which is what fills it in.
    op.add_column("model_registry", sa.Column("decisions_max_choices", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_registry", "decisions_max_choices")

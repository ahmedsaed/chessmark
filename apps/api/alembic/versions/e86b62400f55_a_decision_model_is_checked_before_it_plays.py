"""a decision model is checked before it plays

Revision ID: e86b62400f55
Revises: c2378568ec43
Create Date: 2026-09-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e86b62400f55"
down_revision: str | None = "c2378568ec43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Null for every model, so every decision model is unchecked until the next catalogue refresh
    # asks it once (ADR-0051) — which is also what `d2` needs: `d1` never asked this question shape.
    op.add_column("model_registry", sa.Column("decisions_checked", sa.Text(), nullable=True))
    op.add_column("model_registry", sa.Column("decisions_refusal", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_registry", "decisions_refusal")
    op.drop_column("model_registry", "decisions_checked")

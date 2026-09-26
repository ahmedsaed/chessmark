"""a pool saturates per pair

Revision ID: c2378568ec43
Revises: 20b94cfac74b
Create Date: 2026-09-26 03:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2378568ec43"
down_revision: str | None = "20b94cfac74b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive and null for every existing event, which is exactly what they are today: open-ended
    # pools, pairing for ever (ADR-0050). Setting a target is an operator's step, not a migration's.
    op.add_column("tournaments", sa.Column("games_per_pair", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tournaments", "games_per_pair")

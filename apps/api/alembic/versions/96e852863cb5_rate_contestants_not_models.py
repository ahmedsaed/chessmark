"""rate contestants not models

A contestant is `(model, quantization)`, not a model (ADR-0015). `model@fp4` and `model@fp8`
are different entrants and must be rated apart; averaging them yields a number describing
neither.

Existing rows default to `unknown`, which is honest — they predate precision being part of the
identity. There are none in practice: no ranked game had been played when this was written.

Revision ID: 96e852863cb5
Revises: a2c0aad421ab
Create Date: 2026-08-23 12:36:47.828581
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "96e852863cb5"
down_revision: str | None = "a2c0aad421ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ratings", sa.Column("quantization", sa.Text(), server_default="unknown", nullable=False)
    )
    op.drop_constraint(op.f("uq_ratings_model_id_period"), "ratings", type_="unique")
    op.create_index(op.f("ix_ratings_quantization"), "ratings", ["quantization"], unique=False)
    op.create_unique_constraint(
        "uq_ratings_contestant_period", "ratings", ["model_id", "quantization", "period"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_ratings_contestant_period", "ratings", type_="unique")
    op.drop_index(op.f("ix_ratings_quantization"), table_name="ratings")
    op.create_unique_constraint(
        op.f("uq_ratings_model_id_period"),
        "ratings",
        ["model_id", "period"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("ratings", "quantization")

"""leaderboard snapshot replaces the unused ratings table

The leaderboard is computed when a game ends and stored (ADR-0032), rather than rebuilt from raw
rows on every request.

`ratings` is dropped rather than reused. It was migrated and then never written to — `store_ratings`
had no callers and nothing read the table — and its shape could not hold what the response needs
anyway: the exclusions with their reasons, the aggregate metrics, and the counted set behind each
row all come from the same run. Dropping it loses nothing, because it never held anything.

The new table is a **cache**. It is derived entirely from `games` and is safe to truncate; the next
request rebuilds it.

Revision ID: df060adc0bed
Revises: e7ebb794a626
Create Date: 2026-09-06 14:56:31.941478
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "df060adc0bed"
down_revision: str | None = "e7ebb794a626"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leaderboard_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leaderboard_snapshots")),
        sa.UniqueConstraint("prompt_version", name=op.f("uq_leaderboard_snapshots_prompt_version")),
    )
    op.drop_index(op.f("ix_ratings_model_id"), table_name="ratings")
    op.drop_index(op.f("ix_ratings_period"), table_name="ratings")
    op.drop_index(op.f("ix_ratings_quantization"), table_name="ratings")
    op.drop_table("ratings")


def downgrade() -> None:
    op.create_table(
        "ratings",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("period", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("rating", sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
        sa.Column(
            "rating_deviation",
            sa.DOUBLE_PRECISION(precision=53),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "volatility", sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False
        ),
        sa.Column(
            "games_played",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "computed_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "quantization",
            sa.TEXT(),
            server_default=sa.text("'unknown'::text"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["model_registry.id"],
            name=op.f("fk_ratings_model_id_model_registry"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ratings")),
        sa.UniqueConstraint(
            "model_id",
            "quantization",
            "period",
            name=op.f("uq_ratings_contestant_period"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(op.f("ix_ratings_quantization"), "ratings", ["quantization"], unique=False)
    op.create_index(op.f("ix_ratings_period"), "ratings", ["period"], unique=False)
    op.create_index(op.f("ix_ratings_model_id"), "ratings", ["model_id"], unique=False)
    op.drop_table("leaderboard_snapshots")

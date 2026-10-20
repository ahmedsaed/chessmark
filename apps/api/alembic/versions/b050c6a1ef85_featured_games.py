"""featured_games — the operator's pick for the lobby hero (ADR-0070)

Revision ID: b050c6a1ef85
Revises: 0cc5ce1e26e5
Create Date: 2026-10-20 15:38:35.497946
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b050c6a1ef85"
down_revision: str | None = "0cc5ce1e26e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A new table and nothing else: no game row is touched, so the benchmark's record is exactly
    # what it was before this ran.
    op.create_table(
        "featured_games",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("white_quote", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("black_quote", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("featured_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "featured_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["featured_by_user_id"],
            ["users.id"],
            name=op.f("fk_featured_games_featured_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name=op.f("fk_featured_games_game_id_games"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_featured_games")),
    )
    op.create_index(
        op.f("ix_featured_games_featured_by_user_id"),
        "featured_games",
        ["featured_by_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_featured_games_game_id"), "featured_games", ["game_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_featured_games_game_id"), table_name="featured_games")
    op.drop_index(op.f("ix_featured_games_featured_by_user_id"), table_name="featured_games")
    op.drop_table("featured_games")

"""tail budget: prompt characters, and messages clamped to fit it

Revision ID: 1df24ec621c2
Revises: df060adc0bed
Create Date: 2026-09-08 11:04:39.882315
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1df24ec621c2"
down_revision: str | None = "df060adc0bed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both nullable-or-defaulted with no backfill. A player with no character count yet has a ratio
    # of zero, which tells the planner to fall back to the message ceiling rather than invent one;
    # a message with no `clamped_at` renders exactly as it always did. Nothing is rewritten — the
    # transcript is append-only (ADR-0003).
    op.add_column(
        "players",
        sa.Column("last_prompt_characters", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "transcript_messages",
        sa.Column("clamped_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcript_messages", "clamped_at")
    op.drop_column("players", "last_prompt_characters")

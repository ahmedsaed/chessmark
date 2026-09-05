"""transcript_messages.truncated_at — mark a reply cut off before it acted

Revision ID: e7ebb794a626
Revises: 09d20bf76afb
Create Date: 2026-09-04 18:29:33.627878
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7ebb794a626"
down_revision: str | None = "09d20bf76afb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill: null means "not truncated", which is the truth for every row
    # written before this column existed. Nothing is rewritten — the table is append-only
    # (ADR-0003) and existing games keep replaying exactly what they always did.
    op.add_column(
        "transcript_messages",
        sa.Column("truncated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcript_messages", "truncated_at")

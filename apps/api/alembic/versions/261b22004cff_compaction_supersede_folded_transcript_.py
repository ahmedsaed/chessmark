"""compaction: supersede folded transcript messages

Two nullable-or-defaulted columns on `transcript_messages`, and **no data is rewritten** — which is
the point of the design. A compacted game keeps every message it ever sent; `build_messages` stops
sending the folded ones (ADR-0018). Games that predate compaction have `superseded_at IS NULL` and
`is_summary = false` throughout, which is exactly what an uncompacted transcript means.

`is_summary` takes a server default so the column is populated for existing rows without a
backfill. No new index: `ix_transcript_messages_player_id_seq` already covers the builder's query
shape, and a partial index over the same columns would buy only the skipped archive rows.

Revision ID: 261b22004cff
Revises: 31cfdb2c9cc6
Create Date: 2026-08-27 18:08:20.283434
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "261b22004cff"
down_revision: str | None = "31cfdb2c9cc6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcript_messages",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transcript_messages",
        sa.Column("is_summary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    # A compacted game rolled back to here replays its **whole** history again, folded rows
    # included — correct, and possibly over the window it was compacted to stay inside. Nothing is
    # lost; a long game may simply stop being playable until this is re-applied.
    op.drop_column("transcript_messages", "is_summary")
    op.drop_column("transcript_messages", "superseded_at")

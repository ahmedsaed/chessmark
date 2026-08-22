"""add reasoning_details to transcript_messages

Several models treat their own prior reasoning as part of the history they require back.
Gemini 3 refuses a function call whose `thought_signature` is missing; DeepSeek refuses a
thinking-mode history without `reasoning_content`. OpenRouter normalises both into
`reasoning_details`, and this column is what lets the replayed transcript carry them.

Nullable, so every transcript written before this migration stays readable — those rows
replay without the field, exactly as they were sent at the time.

Revision ID: b9db296afe6c
Revises: 45de46a988d0
Create Date: 2026-08-22 23:52:15.275385
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b9db296afe6c"
down_revision: str | None = "45de46a988d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcript_messages",
        sa.Column("reasoning_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transcript_messages", "reasoning_details")

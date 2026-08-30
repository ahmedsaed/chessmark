"""transcript_messages.trimmed_at — a message kept in the request with its content elided

Rung one of the compaction ladder (ADR-0021, AGENT-20). A stale `get_legal_moves` result is the
bulk of a chess transcript and is worth nothing once the position has moved on, but it cannot
simply stop being sent: the assistant message that requested it would carry a `tool_call_id` with
no answer, which every provider refuses. So the message stays and its content is replaced *in the
request* by a placeholder.

Additive and non-destructive, exactly like `superseded_at`. `content` still holds what the tool
returned, so the record stays verbatim (invariant 3) and a downgrade simply replays the full
results.


Revision ID: 09d20bf76afb
Revises: bd3b3902caa2
Create Date: 2026-08-30 23:48:10.402375
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "09d20bf76afb"
down_revision: str | None = "bd3b3902caa2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcript_messages", sa.Column("trimmed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("transcript_messages", "trimmed_at")

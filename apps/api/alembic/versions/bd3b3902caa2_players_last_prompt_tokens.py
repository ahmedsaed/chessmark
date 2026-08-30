"""players.last_prompt_tokens — the measured prompt size, carried between turns

The count the provider returned, kept on the seat instead of on the `TurnRunner` the worker
rebuilds every turn. Without it the first call of every turn fell back to a character estimate, and
that estimate reported 477,155 tokens of a six-ply transcript against a 256,000-token window
(ADR-0021, AGENT-19).

Zero means "nothing measured yet", which after this migration is briefly true of every seat and
permanently true only before a game's first response. A game in flight across the deploy uses the
first-call bound for one turn and then measures.

Revision ID: bd3b3902caa2
Revises: 81d212a800d5
Create Date: 2026-08-30 23:37:37.037554
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bd3b3902caa2"
down_revision: str | None = "81d212a800d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("last_prompt_tokens", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("players", "last_prompt_tokens")

"""a stored rating run has a scope

Revision ID: 9a854789833f
Revises: 8eac4a8e7efa
Create Date: 2026-10-05 13:41:33.653416
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a854789833f"
down_revision: str | None = "8eac4a8e7efa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The existing row is the leaderboard's, which is what `""` means, so it keeps serving without a
    # rebuild. Pools gain rows of their own on their first read (ADR-0061).
    op.add_column(
        "leaderboard_snapshots",
        sa.Column("scope", sa.Text(), server_default="", nullable=False),
    )
    op.drop_constraint(
        "uq_leaderboard_snapshots_prompt_version", "leaderboard_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_leaderboard_snapshots_prompt_version_scope",
        "leaderboard_snapshots",
        ["prompt_version", "scope"],
    )


def downgrade() -> None:
    # A pool's run would collide with the leaderboard's under the old one-per-prompt key. Every row
    # here is a cache that the next read rebuilds, so the pools' are dropped, not merged.
    op.execute("DELETE FROM leaderboard_snapshots WHERE scope <> ''")
    op.drop_constraint(
        "uq_leaderboard_snapshots_prompt_version_scope", "leaderboard_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_leaderboard_snapshots_prompt_version", "leaderboard_snapshots", ["prompt_version"]
    )
    op.drop_column("leaderboard_snapshots", "scope")

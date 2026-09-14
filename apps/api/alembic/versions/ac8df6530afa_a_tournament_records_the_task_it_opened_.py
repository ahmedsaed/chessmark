"""a tournament records the task it opened on

Revision ID: ac8df6530afa
Revises: 1df24ec621c2
Create Date: 2026-09-14 10:07:06.804183
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ac8df6530afa"
down_revision: str | None = "1df24ec621c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both nullable, and deliberately **not** backfilled.

    `NULL` means unpinned, and an unpinned event never holds. Stamping the deployed versions onto
    events created before this column existed would be a guess — and a wrong one for exactly the
    events that matter, since `pool-free` opened on v2 and `pool-free-v3` on a tool surface that is
    already superseded. Writing today's version onto them would assert they measure today's task,
    which is the claim this column exists to stop being made by accident (ADR-0042).
    """
    op.add_column("tournaments", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.add_column("tournaments", sa.Column("tool_schema_version", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tournaments", "tool_schema_version")
    op.drop_column("tournaments", "prompt_version")

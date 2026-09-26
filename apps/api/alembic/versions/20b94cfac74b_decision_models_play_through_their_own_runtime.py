"""decision models play through their own runtime

Revision ID: 20b94cfac74b
Revises: 7a3c1e9b2f40
Create Date: 2026-09-26 00:49:19.228353
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20b94cfac74b"
down_revision: str | None = "7a3c1e9b2f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME = sa.Enum("llm", "decision", name="modelruntime", native_enum=False, length=40)


def upgrade() -> None:
    # Additive, and every existing row is a chat model, which is what the server default says — so
    # nothing already recorded changes meaning (ADR-0049).
    op.add_column("games", sa.Column("decision_version", sa.Text(), nullable=True))
    op.add_column(
        "model_registry",
        sa.Column("runtime", _RUNTIME, server_default="llm", nullable=False),
    )
    op.add_column(
        "players",
        sa.Column("runtime", _RUNTIME, server_default="llm", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("players", "runtime")
    op.drop_column("model_registry", "runtime")
    op.drop_column("games", "decision_version")

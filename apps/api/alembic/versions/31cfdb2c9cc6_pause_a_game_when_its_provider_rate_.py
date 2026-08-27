"""pause a game when its provider rate-limits it

Two nullable columns and no data migration. `games.status` gains a `paused` value at the same
time, and that needs no DDL: the enum is `native_enum=False` with no CHECK constraint, so Python
is the source of truth for the allowed set (`db/base.py`).

Nothing existing is rewritten. A game paused before this migration cannot exist, and an old
`aborted` game that *was* a rate limit stays aborted — reclassifying finished records from a
string match on their termination detail would be a guess written into history.

Revision ID: 31cfdb2c9cc6
Revises: cc32ea1cb989
Create Date: 2026-08-27 14:48:45.330294
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "31cfdb2c9cc6"
down_revision: str | None = "cc32ea1cb989"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("resume_after", sa.DateTime(timezone=True), nullable=True))
    op.add_column("games", sa.Column("pause_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    # A game left `paused` by the version being rolled back from would strand here: the status
    # survives (it is a string) but nothing would know when to resume it. Resume them before
    # downgrading, or they need `status = 'running'` by hand.
    op.drop_column("games", "pause_reason")
    op.drop_column("games", "resume_after")

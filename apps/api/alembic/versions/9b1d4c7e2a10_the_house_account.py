"""the house account, which pays for games no person started (ADR-0058)

Revision ID: 9b1d4c7e2a10
Revises: 871759676bb6
Create Date: 2026-09-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9b1d4c7e2a10"
down_revision: str | None = "871759676bb6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Created with nothing in it. Money is granted by a person (`./chessmark credits house 20`),
    # never by a migration, so tournaments hold until the house is funded.
    op.execute(
        """
        INSERT INTO users (id, clerk_user_id, display_name, is_admin, balance_usd,
                           created_at, updated_at)
        VALUES (gen_random_uuid(), 'chessmark:house', 'Chessmark', false, 0, now(), now())
        ON CONFLICT (clerk_user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Fails while the house has ledger rows, deliberately: those are a record of real money, and a
    # downgrade shouldn't erase them.
    op.execute("DELETE FROM users WHERE clerk_user_id = 'chessmark:house'")

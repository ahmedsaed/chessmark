"""a pool carries its eras

Revision ID: 06d9450ac683
Revises: 1df24ec621c2
Create Date: 2026-09-14 11:32:27.709891
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "06d9450ac683"
down_revision: str | None = "1df24ec621c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tournament_games", sa.Column("era", sa.Text(), nullable=True))
    op.create_index(op.f("ix_tournament_games_era"), "tournament_games", ["era"])

    # **Backfilled from the games themselves, where there is one** (ADR-0043).
    #
    # The era is the prompt and tool *majors* joined by `+`, so `v2.1` and `v2` land in the same
    # one — the boundary is `same_task`'s, which is what keeps a pool's table and the leaderboard
    # from disagreeing about what counts. `pool-free` therefore sorts itself into `v1+v1` and
    # `v2+v2` rather than arriving as one undifferentiated heap.
    #
    # A pairing with no game gets no era. There is nothing to derive one from, and guessing today's
    # would claim a fixture nobody scheduled belongs to the task now being played — which is
    # exactly the claim this column exists to stop being made by accident. Those rows are inert:
    # `close_stale_pairings` skips a NULL era deliberately, so the last pool's leftovers stay as
    # they are rather than being retired by a migration.
    op.execute(
        """
        UPDATE tournament_games AS tg
           SET era = split_part(COALESCE(g.prompt_version, '?'), '.', 1)
                     || '+' ||
                     split_part(COALESCE(g.tool_schema_version, '?'), '.', 1)
          FROM games AS g
         WHERE g.id = tg.game_id
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tournament_games_era"), table_name="tournament_games")
    op.drop_column("tournament_games", "era")

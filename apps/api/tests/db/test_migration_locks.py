"""A migration must fail fast rather than queue behind a turn (OPS).

**The deploy this exists to prevent.** `./chessmark deploy` migrated while the old worker was still
running, and `ALTER TABLE games ALTER COLUMN ... SET DEFAULT` hung. That statement is a
catalogue-only change taking microseconds, but it needs ACCESS EXCLUSIVE on `games`, and that
conflicts with the ACCESS SHARE that every plain `SELECT` holds. The worker keeps **one transaction
open for a whole turn** (NFR-08) and a free model's turn runs to 442 seconds.

It read as a hang, not a wait, because a queued ACCESS EXCLUSIVE sits at the head of the lock queue
and blocks everything behind it — so the API stalled too.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

#: Short enough that the test is quick, long enough not to be flaky on a loaded machine.
TIMEOUT_SECONDS = 2


async def test_a_reader_blocks_a_column_default_change(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The conflict itself, so the reasoning above is checked rather than asserted in a comment.

    An ordinary `SELECT` is enough. Nothing needs to be writing, and nothing needs to touch the
    column being altered.
    """
    await db.execute(sa.text("SELECT count(*) FROM games"))  # ACCESS SHARE, held by this txn

    async with sessionmaker() as other:
        await other.execute(sa.text(f"SET lock_timeout = '{TIMEOUT_SECONDS}s'"))
        started = time.perf_counter()

        with pytest.raises(Exception, match="lock timeout"):
            await other.execute(
                sa.text("ALTER TABLE games ALTER COLUMN auto_threefold_draw SET DEFAULT false")
            )

        waited = time.perf_counter() - started
        await other.rollback()

    assert waited >= TIMEOUT_SECONDS, "it really did wait for the lock"
    assert waited < TIMEOUT_SECONDS + 10, "and gave up rather than waiting out the reader"


def test_the_alembic_environment_sets_a_lock_timeout() -> None:
    """Without this the migration waits for ever, which is what happened.

    Read as text rather than imported: importing `alembic/env.py` runs migrations.
    """
    env = Path(__file__).resolve().parents[2] / "alembic" / "env.py"
    source = env.read_text()

    assert "lock_timeout" in source, "a migration with no lock timeout can hang a deploy"
    assert "ALEMBIC_LOCK_TIMEOUT_SECONDS" in source, (
        "and it must be overridable, for a backfill that legitimately needs to wait"
    )

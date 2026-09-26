"""The tournament page says when a pool is idle because every pair has played (ADR-0050)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db.models import Tournament
from tests.tournament.test_a_pool_saturates import _play_out, _pool

pytestmark = pytest.mark.integration


async def test_the_page_says_a_saturated_pool_is_idle(
    db: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: object,
    client: AsyncClient,
) -> None:
    tid = await _pool(db, models=2, per_pair=1)
    tournament = await db.get(Tournament, tid)
    assert tournament is not None
    slug = tournament.slug

    before = (await client.get(f"/tournaments/{slug}")).json()
    assert before["games_per_pair"] == 1 and before["saturated"] is False

    await _play_out(db, sessionmaker, queue, tid)
    after = (await client.get(f"/tournaments/{slug}")).json()
    assert after["saturated"] is True

"""Changing a running event's concurrency (OPS-22).

`--max-concurrent` was settable only at `create`, which is the one moment nobody knows the right
value: it depends on how many workers are up, how hot the free pools are that day, and how long a
turn is taking. `pool-free` ran at 1 for a week because changing it meant a hand-written UPDATE.

The floor is the interesting part. A concurrency of zero reads as "run nothing" and *would* do
exactly that — the runner ticking quietly forever, starting no games and reporting no error, which
is a way of pausing an event that leaves no record of having been chosen. `pause` says so out loud.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db import tournaments as repo
from chessmark.db.models import Tournament
from chessmark.tournament import FieldFilter, Format, TournamentConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_cli = importlib.import_module("tournament")

pytestmark = pytest.mark.integration


async def _event(db: AsyncSession, *, concurrent: int) -> uuid.UUID:
    """The id, not the instance: the tests expire the session to see what was committed, and a live
    ORM object would then try to refresh itself outside async context."""
    tournament = await repo.create_tournament(
        db,
        name="Set Test",
        slug="set-test",
        config=TournamentConfig(format=Format.POOL, max_concurrent=concurrent, field=FieldFilter()),
        entrants=[],
    )
    created = tournament.id
    await db.commit()
    return created


async def test_it_raises_the_bound(db: AsyncSession) -> None:
    event_id = await _event(db, concurrent=1)

    was, now = await _cli.apply_concurrency(db, slug="set-test", value=4)
    await db.commit()

    assert (was, now) == (1, 4)
    db.expire_all()
    reloaded = await db.get(Tournament, event_id)
    assert reloaded is not None and reloaded.max_concurrent == 4


async def test_it_lowers_the_bound(db: AsyncSession) -> None:
    """Lowering stops nothing already running — the runner and the reconciler simply start nothing
    new until the count falls (ADR-0025). All this does is write the number."""
    event_id = await _event(db, concurrent=4)

    await _cli.apply_concurrency(db, slug="set-test", value=1)
    await db.commit()

    db.expire_all()
    reloaded = await db.get(Tournament, event_id)
    assert reloaded is not None and reloaded.max_concurrent == 1


async def test_an_unknown_slug_is_refused(db: AsyncSession) -> None:
    """`resolve_slug` raises rather than creating, so a typo cannot quietly configure nothing."""
    await _event(db, concurrent=1)

    with pytest.raises(SystemExit):
        await _cli.apply_concurrency(db, slug="no-such-event", value=2)


def test_the_floor_is_one_not_zero() -> None:
    """Asserted as a policy rather than an implementation detail: a bound of zero is a pause that
    does not say it is one, and an event stopped that way reports itself as running forever."""
    assert _cli.MIN_CONCURRENT == 1

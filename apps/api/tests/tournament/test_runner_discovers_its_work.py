"""The runner finds its own tournaments (OPS-24).

The long-running container used to tick whichever slug `TOURNAMENT_SLUG` named:

    command: ["run", "${TOURNAMENT_SLUG:-pool-free}", "--interval", "30"]

So creating a tournament through the CLI produced an event nothing would ever tick, and retiring
one left the container ticking a corpse. Both failures are silent — the pool simply sits at zero
pairings, looking like a matchmaker that cannot find a game — and both needed an edit to `.env` and
a container restart to fix, which is a deploy-shaped action for what ought to be
`tournament create`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import TournamentStatus
from chessmark.db.models import Tournament

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_runner = importlib.import_module("tournament")

pytestmark = pytest.mark.integration


async def _make(db: AsyncSession, slug: str, status: TournamentStatus) -> Tournament:
    from chessmark.db.tournaments import create_tournament
    from chessmark.tournament import FieldFilter, Format, TournamentConfig

    tournament = await create_tournament(
        db,
        name=slug,
        slug=slug,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        entrants=[],
    )
    tournament.status = status
    await db.commit()
    return tournament


class TestWhatGetsTicked:
    """`FINISHED` and `ABANDONED` are over. The other three are not, and each for its own reason."""

    @pytest.mark.parametrize(
        "status",
        [TournamentStatus.PENDING, TournamentStatus.RUNNING, TournamentStatus.PAUSED],
    )
    async def test_an_unfinished_event_is_ticked(
        self, db: AsyncSession, sessionmaker, status: TournamentStatus
    ) -> None:
        """`PENDING` needs a first tick to start at all, and `PAUSED` needs one to notice it has
        been resumed — a resume that no tick follows is a resume that does nothing."""
        await _make(db, f"pool-{status.value}", status)

        found = await _runner._tickable(sessionmaker)

        assert [name for _, name in found] == [f"pool-{status.value}"]

    @pytest.mark.parametrize("status", [TournamentStatus.FINISHED, TournamentStatus.ABANDONED])
    async def test_a_finished_event_is_left_alone(
        self, db: AsyncSession, sessionmaker, status: TournamentStatus
    ) -> None:
        await _make(db, f"pool-{status.value}", status)

        assert await _runner._tickable(sessionmaker) == []

    async def test_several_events_are_all_ticked(self, db: AsyncSession, sessionmaker) -> None:
        """The property the env pin could not have: two pools run at once, each with its own
        concurrency, and neither needs a container of its own."""
        await _make(db, "pool-free-v3", TournamentStatus.RUNNING)
        await _make(db, "pool-paid", TournamentStatus.RUNNING)

        assert len(await _runner._tickable(sessionmaker)) == 2

    async def test_nothing_to_tick_is_not_an_error(self, db: AsyncSession, sessionmaker) -> None:
        """A fresh deployment has no tournament until somebody creates one, and the container must
        wait for it rather than crash-loop."""
        assert await _runner._tickable(sessionmaker) == []

    async def test_a_new_event_is_picked_up_without_a_restart(
        self, db: AsyncSession, sessionmaker
    ) -> None:
        """The whole point. `tournament create` is enough; no `.env` edit, no restart."""
        assert await _runner._tickable(sessionmaker) == []

        await _make(db, "pool-free-v3", TournamentStatus.RUNNING)

        assert [name for _, name in await _runner._tickable(sessionmaker)] == ["pool-free-v3"]

    async def test_a_retired_event_stops_being_ticked(self, db: AsyncSession, sessionmaker) -> None:
        """And the other half: `tournament abandon` is enough to stop it."""
        tournament = await _make(db, "pool-free", TournamentStatus.RUNNING)
        assert await _runner._tickable(sessionmaker)

        await db.execute(
            sa.update(Tournament)
            .where(Tournament.id == tournament.id)
            .values(status=TournamentStatus.ABANDONED)
        )
        await db.commit()

        assert await _runner._tickable(sessionmaker) == []

    async def test_the_order_is_stable_across_passes(self, db: AsyncSession, sessionmaker) -> None:
        """Ordered by creation, so a pass does not reshuffle which event gets the first look at a
        shared free-tier allowance."""
        for slug in ("a", "b", "c"):
            await _make(db, f"pool-{slug}", TournamentStatus.RUNNING)

        first = await _runner._tickable(sessionmaker)
        second = await _runner._tickable(sessionmaker)

        assert first == second
        assert [name for _, name in first] == ["pool-a", "pool-b", "pool-c"]

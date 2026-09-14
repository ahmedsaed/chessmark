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
from chessmark.orchestration.tournament import _stale_task

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


# ================================================ an event measures the task it opened on


class TestAStaleTask:
    """A tournament records its prompt and tool versions at creation (ADR-0042).

    It used to record neither, so a deploy that changed the task changed what a running pool
    measured — silently, and mid-crosstable. `pool-free` went on pairing under v3 into a v2 table.
    `pool-free-v3` settled three games under a tool surface whose `get_legal_moves` still named the
    mating move: the list came back with forty-five moves sorted alphabetically, exactly one `#`
    among them, and the model played it.
    """

    async def test_a_new_event_records_what_was_deployed(self, db: AsyncSession) -> None:
        from chessmark.agents.prompts import PROMPT_VERSION
        from chessmark.agents.tools import TOOL_SCHEMA_VERSION

        tournament = await _make(db, "pool-now", TournamentStatus.RUNNING)

        assert tournament.prompt_version == PROMPT_VERSION
        assert tournament.tool_schema_version == TOOL_SCHEMA_VERSION

    async def test_a_stale_prompt_holds_the_event(self, db: AsyncSession) -> None:
        tournament = await _make(db, "pool-old-prompt", TournamentStatus.RUNNING)
        tournament.prompt_version = "v1"
        await db.commit()

        holding = _stale_task(tournament)

        assert "prompt v1" in holding
        assert "create a new event" in holding, "it says what to do, not only that it stopped"

    async def test_a_stale_tool_surface_holds_it_too(self, db: AsyncSession) -> None:
        """The half a prompt version cannot describe — and the one that actually bit."""
        tournament = await _make(db, "pool-old-tools", TournamentStatus.RUNNING)
        tournament.tool_schema_version = "v3"
        await db.commit()

        assert "tool schema v3" in _stale_task(tournament)

    async def test_a_minor_bump_does_not_hold_it(self, db: AsyncSession) -> None:
        """The same major/minor rule the leaderboard uses: the same task, stated more
        conveniently, is not a reason to stop an event (ADR-0038)."""
        from chessmark.agents.prompts import PROMPT_VERSION

        tournament = await _make(db, "pool-minor", TournamentStatus.RUNNING)
        tournament.prompt_version = f"{PROMPT_VERSION}.7"
        await db.commit()

        assert _stale_task(tournament) == ""

    async def test_an_unpinned_event_never_holds(self, db: AsyncSession) -> None:
        """Every event created before the column existed carries `NULL`, and a column added today
        must not stop one that was running yesterday. The migration deliberately does not backfill:
        writing today's version onto them would assert they measure today's task, which is the
        claim this exists to stop being made by accident."""
        tournament = await _make(db, "pool-unpinned", TournamentStatus.RUNNING)
        tournament.prompt_version = None
        tournament.tool_schema_version = None
        await db.commit()

        assert _stale_task(tournament) == ""

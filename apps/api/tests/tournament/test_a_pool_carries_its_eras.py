"""A pool never ends, so it carries its version changes inside itself (ADR-0043).

We reset a pool by hand three times in two days — `pool-free` → `pool-free-v3` → `pool-free-v4` —
because a prompt or tool bump changed what a running event was measuring and nothing noticed. The
slugs were the giveaway: a pool is *defined* by not ending, so an event abandoned and recreated per
version is a series of tournaments wearing a pool's name.

An era is the prompt and tool **majors** joined by `+`. Bumping either opens a new one on the next
tick; a minor bump does not, because the boundary here has to be the same one `same_task` draws or
a pool's table and the leaderboard could disagree about what counts.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.bench.ratable import era
from chessmark.db import tournaments as repo
from chessmark.db.models import ModelRegistry, Tournament, TournamentGame
from chessmark.orchestration.tournament import advance
from chessmark.tournament import FieldFilter, Format, Pairing, TournamentConfig
from tests.tournament.test_runner import abandon_all_in_flight, make_tournament

pytestmark = pytest.mark.integration


class TestWhatAnEraIs:
    """Pure, and worth pinning separately: the whole design rests on where this boundary falls."""

    def test_it_is_the_two_majors(self) -> None:
        assert era("v3", "v4") == "v3+v4"

    def test_a_minor_bump_stays_in_the_same_era(self) -> None:
        """v2 → v2.1 stated the same task more conveniently (ADR-0038). Splitting an era on it
        would throw away a round robin to record a distinction the leaderboard does not make."""
        assert era("v3.7", "v4") == era("v3", "v4")

    @pytest.mark.parametrize(
        ("prompt", "tools"), [("v4", "v4"), ("v3", "v5")], ids=["prompt", "tools"]
    )
    def test_bumping_either_half_opens_a_new_one(self, prompt: str, tools: str) -> None:
        """The composite is the point: the prompt is half the task and the tools are the other
        half, and ADR-0042 was the day that stopped being theoretical."""
        assert era(prompt, tools) != era("v3", "v4")

    def test_an_unversioned_game_is_its_own_era(self) -> None:
        """From before the field existed — it measured something we cannot name, so it must not
        land in the same bucket as anything we can."""
        assert era(None, None) == "?+?"
        assert era(None, "v4") != era("v3", "v4")


class TestThePairingRemembersItsEra:
    async def test_a_new_pairing_joins_the_era_being_played(
        self, db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
    ) -> None:
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )

        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()

        rows = await repo.in_flight(db, tournament_id)
        assert rows and all(row.era == repo.current_era() for row in rows)


class TestTheMatchmakersMemoryIsScoped:
    """**The load-bearing half, and the one easy to miss.**

    `results_so_far` and `attempted` are what the balance policy reads to decide who has met whom
    (ADR-0041). If they still saw the old era after a bump, the pool would open its new era
    convinced every pair had already played — rematching immediately while models that had never
    met under the new rules waited. Scoping the *display* alone would leave that intact and
    invisible.
    """

    async def test_an_old_eras_results_are_not_read(self, db: AsyncSession) -> None:
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-0", black="model-1", round_number=1)]
        )
        rows = await repo.unplayed(db, tournament_id)
        rows[0].era = "v1+v1"
        rows[0].white_score = 1.0
        await db.commit()

        assert await repo.results_so_far(db, tournament_id, era="v9+v9") == []
        assert len(await repo.results_so_far(db, tournament_id, era="v1+v1")) == 1
        assert len(await repo.results_so_far(db, tournament_id)) == 1, "no era means every era"

    async def test_an_old_eras_attempt_is_not_a_rematch(self, db: AsyncSession) -> None:
        """The other half. An unsettled pairing counts as a meeting (OPS-21) — but only within the
        era that scheduled it."""
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-0", black="model-1", round_number=1)]
        )
        rows = await repo.unplayed(db, tournament_id)
        rows[0].era = "v1+v1"
        await db.commit()

        assert await repo.attempted(db, tournament_id, era="v9+v9") == []
        assert len(await repo.attempted(db, tournament_id, era="v1+v1")) == 1


class TestARolledEra:
    async def test_a_stale_unplayed_pairing_is_retired(self, db: AsyncSession) -> None:
        """It will never run — the matchmaker is pairing for the new era now — and a fixture on
        the table that nothing will ever start is a lie. Marked rather than deleted, so the old
        era's crosstable still shows what it had planned when it ended.
        """
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-0", black="model-1", round_number=1)]
        )
        rows = await repo.unplayed(db, tournament_id)
        rows[0].era = "v1+v1"
        await db.commit()

        closed = await repo.close_stale_pairings(db, tournament_id, era="v3+v4")
        await db.commit()

        assert closed == 1
        row = await db.get(TournamentGame, rows[0].id)
        assert row is not None and "moved on to v3+v4" in (row.abandoned_reason or "")

    async def test_the_current_eras_pairings_are_left_alone(self, db: AsyncSession) -> None:
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-0", black="model-1", round_number=1)]
        )
        await db.commit()

        assert await repo.close_stale_pairings(db, tournament_id, era=repo.current_era()) == 0

    async def test_a_pairing_with_no_era_is_never_retired(self, db: AsyncSession) -> None:
        """Every row written before the column existed. The migration deliberately leaves them
        `NULL` rather than guessing, and a guess made here would be the same mistake later."""
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-0", black="model-1", round_number=1)]
        )
        rows = await repo.unplayed(db, tournament_id)
        rows[0].era = None
        await db.commit()

        assert await repo.close_stale_pairings(db, tournament_id, era="v3+v4") == 0

    async def test_a_game_already_in_flight_finishes_in_its_own_era(
        self, db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
    ) -> None:
        """Only rows with no game are retired. A game that is being played belongs to the era that
        scheduled it and scores there — ending it because the harness was deployed over would be
        the harness taking a result off a model (invariant 11)."""
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await advance(sessionmaker, queue, tournament_id=tournament_id)
        db.expire_all()
        live = await repo.in_flight(db, tournament_id)
        assert live

        assert await repo.close_stale_pairings(db, tournament_id, era="v9+v9") == 0

        await abandon_all_in_flight(db, tournament_id)


class TestTheErasAnEventHasPlayed:
    async def test_they_are_read_from_the_pairings(self, db: AsyncSession) -> None:
        """Derived rather than stored on the tournament: which eras an event has been through is a
        fact about what it played, and a second copy could disagree with it."""
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-0", black="model-1", round_number=1)]
        )
        await repo.record_round(
            db, tournament_id, [Pairing(white="model-2", black="model-3", round_number=2)]
        )
        rows = sorted(await repo.unplayed(db, tournament_id), key=lambda r: r.round_number)
        rows[0].era = "v1+v1"
        await db.commit()

        assert await repo.eras_of(db, tournament_id) == [repo.current_era(), "v1+v1"], (
            "newest first, so the dropdown opens on what is being played"
        )

    async def test_an_event_that_has_played_nothing_has_no_eras(self, db: AsyncSession) -> None:
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )

        assert await repo.eras_of(db, tournament_id) == []


async def departs(db: AsyncSession) -> str:
    """Take one seeded model out of the field, the way the catalogue takes one out: disabled.

    `seed_models` never deletes — a model that disappears from OpenRouter is disabled and its games
    stay readable — so this is the real shape of leaving, not a contrived one.
    """
    model = await db.scalar(
        sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == "vendor/model-1")
    )
    assert model is not None
    model.enabled = False
    await db.commit()
    return model.openrouter_id


class TestAnEntrantThatLeftTheField:
    """**Keeping a record and handing out games are different instructions.**

    A pool re-resolves its field every tick to seat newcomers and deliberately never withdraws a
    model that has left — its games are real results and its rating is real, and dropping it because
    an endpoint went quiet would rewrite history (`admit_new_entrants`). The pool was reading that
    as "so keep pairing it".

    `pool-free` seated 19 while the free tier served 16: `minimax-m2.7`, `minimax-m3` and `glm-5.2`
    were gone from OpenRouter's free catalogue and still being handed games — which the balance
    policy *prioritises*, because they have the fewest (ADR-0041). Three delisted models were taking
    the pool's scarcest resource ahead of models that could actually play.
    """

    async def test_a_departed_entrant_is_not_paired(
        self, db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue
    ) -> None:
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        gone = await departs(db)

        for _ in range(3):
            await advance(sessionmaker, queue, tournament_id=tournament_id)
            db.expire_all()
            await abandon_all_in_flight(db, tournament_id)

        paired = {
            key
            for row in await repo.attempted(db, tournament_id)
            for key in (row.white, row.black)
            if key
        }
        assert gone not in paired, (
            "a model the field would no longer admit was handed a game — and the balance policy "
            "puts it first, because it has the fewest"
        )

    async def test_its_record_is_kept(self, db: AsyncSession) -> None:
        """The other half, and the reason this is not a withdrawal: the games it already played are
        real results, and its row stays on the table."""
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        gone = await departs(db)

        seated = {e.key for e in await repo.entrants_of(db, tournament_id)}
        assert gone in seated, "still seated — this is not a withdrawal"

    async def test_the_table_can_tell_which_rows_are_closed(self, db: AsyncSession) -> None:
        """A reader looking at a row that will never gain another game deserves to be told."""
        tournament_id, _ = await make_tournament(
            db,
            models=4,
            config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        )
        tournament = await db.get(Tournament, tournament_id)
        assert tournament is not None
        gone = await departs(db)

        playable = await repo.in_field(
            db, tournament, repo.filter_from_json(tournament.field_filter)
        )

        assert gone not in playable
        assert len(playable) == 3, playable

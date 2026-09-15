"""The tournament endpoints (BENCH-05).

The load-bearing assertion is the pairing *state*: a page that says a game is queued when it has
already been played, or live when it abandoned, is worse than no page. State is derived from the
row rather than stored, and this pins that derivation.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.db import tournaments as repo
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, ModelEndpoint, ModelRegistry, TournamentGame
from chessmark.game import GameResult, Termination
from chessmark.tournament import FieldFilter, Format, Pairing, TournamentConfig

pytestmark = pytest.mark.integration


async def make_event(db: AsyncSession, *, models: int = 4, slug: str = "test-cup"):
    # Namespaced by the event, so a test creating two of them does not re-register the same
    # models — the endpoint table is unique on (model, provider).
    slugs = [f"vendor/{slug}-m{i}" for i in range(1, models + 1)]
    await sync_model_registry(
        db,
        [
            {
                "openrouter_id": s,
                "display_name": s.upper(),
                "context_length": 200_000,
                "supports_tools": True,
            }
            for s in slugs
        ],
    )
    await db.flush()
    for s in slugs:
        model_id = await db.scalar(
            sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == s)
        )
        db.add(
            ModelEndpoint(model_id=model_id, provider_name="P", supports_tools=True, is_active=True)
        )
    await db.commit()

    config = TournamentConfig(format=Format.ROUND_ROBIN, field=FieldFilter(free_only=False))
    entrants = await repo.resolve_field(db, config.field)
    tournament = await repo.create_tournament(
        db, name="Test Cup", slug=slug, config=config, entrants=entrants
    )
    created = tournament.id
    await db.commit()
    return created, entrants


async def test_a_tournament_page_is_public(client: AsyncClient, db: AsyncSession) -> None:
    """Reading is open to everyone (AUTH-02) — a tournament is a spectacle."""
    await make_event(db)

    assert (await client.get("/tournaments/test-cup")).status_code == 200


async def test_an_unknown_slug_is_a_404_not_a_crash(client: AsyncClient) -> None:
    response = await client.get("/tournaments/never-happened")

    assert response.status_code == 404
    assert "never have been created" in response.text


async def test_the_page_reports_its_field_and_bounds(client: AsyncClient, db: AsyncSession) -> None:
    """A standings table that cannot say who was invited is not explicable."""
    await make_event(db, models=4)

    body = (await client.get("/tournaments/test-cup")).json()

    assert body["entrant_count"] == 4
    assert body["format"] == "round_robin"
    assert body["field_description"]
    assert len(body["standings"]) == 4


async def test_pairings_report_the_state_they_are_actually_in(
    client: AsyncClient, db: AsyncSession
) -> None:
    """queued / live / played / abandoned, derived from the row rather than stored."""
    tournament_id, _ = await make_event(db, models=4)
    rows = list(
        await db.scalars(
            sa.select(TournamentGame)
            .where(TournamentGame.tournament_id == tournament_id)
            .order_by(TournamentGame.id)
        )
    )
    assert not rows, "nothing is scheduled until the runner ticks"

    from chessmark.tournament import round_robin

    entrants = await repo.entrants_of(db, tournament_id)
    for games in round_robin(entrants):
        await repo.record_round(db, tournament_id, games)
    await db.commit()

    rows = list(
        await db.scalars(
            sa.select(TournamentGame)
            .where(TournamentGame.tournament_id == tournament_id)
            .order_by(TournamentGame.id)
        )
    )
    # One played, one live, one abandoned, the rest queued. **The live one needs a game that is
    # actually running**: this used to point at a FINISHED game and still expect "live", which was
    # the bug wearing an assertion — a pairing whose game has ended is played, whatever its own
    # columns say yet.
    finished = Game(status=GameStatus.FINISHED, start_fen="8/8/8/8/8/8/8/8 w - - 0 1")
    running = Game(status=GameStatus.RUNNING, start_fen="8/8/8/8/8/8/8/8 w - - 0 1")
    db.add_all([finished, running])
    await db.flush()
    rows[0].white_score = 1.0
    rows[0].game_id = finished.id
    rows[1].game_id = running.id
    rows[2].abandoned_reason = "no provider could be reached"
    await db.commit()

    body = (await client.get("/tournaments/test-cup")).json()
    states = [p["state"] for p in body["pairings"]]

    assert states.count("played") == 1
    assert states.count("live") == 1
    assert states.count("abandoned") == 1
    assert states.count("waiting") == len(rows) - 3
    assert body["stats"]["pairings"] == len(rows)


async def test_a_resumed_game_outranks_the_verdict_its_pairing_still_holds(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The game record is the authority (invariant 1), and a resumed game makes its pairing's own
    columns stale — so the state must be read from the game, not from them.

    Both halves were seen on the same page at once. Four pairings kept the score of a forfeit that
    had just been overturned, so games running at up to ply 89 were drawn as *played* and the event
    reported `live: 0` while four boards moved; and a game abandoned on a provider 404, resumed and
    played on to checkmate at ply 120, was still drawn as *abandoned*. `resume_game.py` clears both
    columns now; this is the half that stops a stale one from being believed if it survives.
    """
    tournament_id, _ = await make_event(db, models=4)

    from chessmark.tournament import round_robin

    entrants = await repo.entrants_of(db, tournament_id)
    for games in round_robin(entrants):
        await repo.record_round(db, tournament_id, games)
    await db.commit()

    rows = list(
        await db.scalars(
            sa.select(TournamentGame)
            .where(TournamentGame.tournament_id == tournament_id)
            .order_by(TournamentGame.id)
        )
    )

    resumed_from_forfeit = Game(status=GameStatus.RUNNING, start_fen="8/8/8/8/8/8/8/8 w - - 0 1")
    resumed_to_a_result = Game(
        status=GameStatus.FINISHED, start_fen="8/8/8/8/8/8/8/8 w - - 0 1", ply_count=120
    )
    db.add_all([resumed_from_forfeit, resumed_to_a_result])
    await db.flush()

    rows[0].game_id = resumed_from_forfeit.id
    rows[0].white_score = 1.0  # the overturned forfeit's score, not yet cleared
    rows[1].game_id = resumed_to_a_result.id
    rows[1].abandoned_reason = "provider returned 404"  # the abandonment it outgrew
    await db.commit()

    body = (await client.get("/tournaments/test-cup")).json()
    by_game = {p["game_id"]: p["state"] for p in body["pairings"] if p["game_id"]}

    assert by_game[str(resumed_from_forfeit.id)] == "live", (
        "a running game is live however its pairing was scored"
    )
    assert by_game[str(resumed_to_a_result.id)] == "played", (
        "a finished game is played however its pairing was abandoned"
    )
    assert body["stats"]["live"] == 1, "the count the page leads with has to agree"


async def test_money_comes_from_the_games_not_a_running_total(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A tournament page must not print a cost the call log disagrees with (invariant 4)."""
    from decimal import Decimal

    from chessmark.tournament import round_robin

    tournament_id, _ = await make_event(db, models=2)
    entrants = await repo.entrants_of(db, tournament_id)
    for games in round_robin(entrants):
        await repo.record_round(db, tournament_id, games)
    await db.commit()

    row = (
        await db.scalars(
            sa.select(TournamentGame).where(TournamentGame.tournament_id == tournament_id)
        )
    ).first()
    assert row is not None
    game = Game(
        status=GameStatus.FINISHED,
        result=GameResult.WHITE_WINS,
        termination=Termination.CHECKMATE,
        start_fen="8/8/8/8/8/8/8/8 w - - 0 1",
        ply_count=31,
        total_cost_usd=Decimal("0.1234"),
        total_tokens=5000,
    )
    db.add(game)
    await db.flush()
    row.game_id = game.id
    row.white_score = 1.0
    await db.commit()

    stats = (await client.get("/tournaments/test-cup")).json()["stats"]

    assert stats["total_cost_usd"] == "0.12340000"
    assert stats["total_tokens"] == 5000
    assert stats["total_plies"] == 31
    assert stats["decisive"] == 1
    assert stats["draws"] == 0


async def test_the_listing_shows_recent_events(client: AsyncClient, db: AsyncSession) -> None:
    await make_event(db, slug="cup-one")
    await make_event(db, slug="cup-two", models=2)

    body = (await client.get("/tournaments")).json()

    assert {row["slug"] for row in body} >= {"cup-one", "cup-two"}


async def test_a_live_game_is_not_counted_as_a_decisive_result(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A game still being played carries `ongoing`, which is not `draw`.

    Counting "not a draw" therefore reported a decisive result for a game nobody had won — the
    page said one game was won while also saying none had been played.
    """
    from chessmark.tournament import round_robin

    tournament_id, _ = await make_event(db, models=2, slug="live-cup")
    entrants = await repo.entrants_of(db, tournament_id)
    for games in round_robin(entrants):
        await repo.record_round(db, tournament_id, games)
    await db.commit()

    row = (
        await db.scalars(
            sa.select(TournamentGame).where(TournamentGame.tournament_id == tournament_id)
        )
    ).first()
    assert row is not None
    game = Game(status=GameStatus.RUNNING, start_fen="8/8/8/8/8/8/8/8 w - - 0 1", ply_count=4)
    db.add(game)
    await db.flush()
    row.game_id = game.id
    await db.commit()

    stats = (await client.get("/tournaments/live-cup")).json()["stats"]

    assert stats["live"] == 1
    assert stats["played"] == 0
    assert stats["decisive"] == 0, "nobody has won anything yet"
    assert stats["draws"] == 0
    assert stats["mean_plies"] is None, "no finished game to average over"


# ====================================================================== eras (ADR-0043)


class TestTheNumbersMatchTheTable:
    """The header and the crosstable have to be about the same games.

    `_stats` counted every pairing an event had ever had, which was right until an event could have
    more than one era. Left alone it would report `pool-free`'s 123 pairings and 88 played above a
    `v3+v4` table with four rows in it — two different events on one page.
    """

    async def test_the_counts_are_scoped_to_the_era_shown(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        tournament_id, _ = await make_event(db, slug="eras-scoped")
        await repo.record_round(db, tournament_id, [Pairing(white="a", black="b", round_number=99)])
        stale = [r for r in await repo.unplayed(db, tournament_id) if r.round_number == 99]
        stale[0].era = "v1+v1"
        # ...and one from the task being played now, so there are two eras to choose between.
        await repo.record_round(
            db, tournament_id, [Pairing(white="c", black="d", round_number=100)]
        )
        await db.commit()

        body = (await client.get("/tournaments/eras-scoped")).json()

        assert body["era"] == repo.current_era()
        assert body["eras"] == [repo.current_era(), "v1+v1"]
        assert all(p["round_number"] != 99 for p in body["pairings"]), (
            "the stale era's fixture is not on the current table"
        )

    async def test_an_older_era_can_be_asked_for(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        tournament_id, _ = await make_event(db, slug="eras-older")
        await repo.record_round(db, tournament_id, [Pairing(white="a", black="b", round_number=99)])
        stale = [r for r in await repo.unplayed(db, tournament_id) if r.round_number == 99]
        stale[0].era = "v1+v1"
        # ...and one from the task being played now, so there are two eras to choose between.
        await repo.record_round(
            db, tournament_id, [Pairing(white="c", black="d", round_number=100)]
        )
        await db.commit()

        body = (await client.get("/tournaments/eras-older?era=v1%2Bv1")).json()

        assert body["era"] == "v1+v1"
        assert body["stats"]["pairings"] == 1
        assert [p["round_number"] for p in body["pairings"]] == [99]

    async def test_an_unknown_era_falls_back_rather_than_404ing(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """A stale link should show the current table, not an error."""
        tournament_id, _ = await make_event(db, slug="eras-unknown")
        await repo.record_round(db, tournament_id, [Pairing(white="a", black="b", round_number=99)])
        await db.commit()

        response = await client.get("/tournaments/eras-unknown?era=nonsense")

        assert response.status_code == 200
        assert response.json()["era"] == repo.current_era()


# ========================================================= a game says which event it belongs to


async def _seat_a_tournament_game(
    db: AsyncSession,
    tournament_id,
    *,
    era: str | None = "v3+v4",
    white: str = "scripted/white",
    black: str | None = "scripted/black",
):
    """Create a real game and record it as an event pairing, the way the runner would."""
    from chessmark.orchestration.match import Seat, create_match

    match = await create_match(
        db,
        white=Seat(display_name="w", model="scripted/white"),
        black=Seat(display_name="b", model="scripted/black"),
    )
    db.add(
        TournamentGame(
            tournament_id=tournament_id,
            era=era,
            round_number=217,
            white_key=white,
            black_key=black,
            game_id=match.game.id,
        )
    )
    await db.commit()
    return match.game.id


async def test_the_event_card_places_both_seats(client: AsyncClient, db: AsyncSession) -> None:
    """The card is a two-row slice of the standings table, so the rows have to be *these* two
    seats — matched by the pairing's entrant keys rather than guessed from a model slug."""
    tournament_id, entrants = await make_event(db)
    keys = [e.key for e in entrants]
    game_id = await _seat_a_tournament_game(db, tournament_id, white=keys[0], black=keys[1])

    body = (await client.get(f"/games/{game_id}/event")).json()

    assert body["tournament"]["slug"] == "test-cup"
    assert body["ranked_by"] == "score", "a round robin is ranked by points, not rating (ADR-0027)"
    assert body["entrants"] == 4
    assert [seat["colour"] for seat in body["seats"]] == ["white", "black"]
    assert [seat["key"] for seat in body["seats"]] == [keys[0], keys[1]]
    assert all(seat["place"] is not None for seat in body["seats"])


async def test_a_seat_outside_the_field_keeps_its_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A human, or a model seated outside the field. The row stays and says so — dropping it would
    leave a one-row card that reads as a rendering fault rather than as the truth."""
    tournament_id, entrants = await make_event(db)
    keys = [e.key for e in entrants]
    game_id = await _seat_a_tournament_game(
        db, tournament_id, white=keys[0], black="somebody/not-entered"
    )

    seats = (await client.get(f"/games/{game_id}/event")).json()["seats"]

    assert seats[0]["place"] is not None
    assert seats[1]["place"] is None and seats[1]["display_name"]


async def test_a_game_outside_any_event_has_no_card(client: AsyncClient, db: AsyncSession) -> None:
    from chessmark.orchestration.match import Seat, create_match

    match = await create_match(
        db,
        white=Seat(display_name="w", model="scripted/white"),
        black=Seat(display_name="b", model="scripted/black"),
    )
    await db.commit()

    assert (await client.get(f"/games/{match.game.id}/event")).status_code == 404


async def test_the_card_reads_the_games_own_era(client: AsyncClient, db: AsyncSession) -> None:
    """**Not today's table** (ADR-0043). A pool carries its task changes inside itself, so a table
    built from every era at once would rank these two models partly on games played under a
    different prompt with a different tool surface — the comparison eras exist to prevent.

    Here the game belongs to an era with no results at all, so both seats read nought played. Were
    the endpoint pooling every era, the other era's game would show up in these rows.
    """
    tournament_id, entrants = await make_event(db)
    keys = [e.key for e in entrants]

    # A settled pairing in one era...
    db.add(
        TournamentGame(
            tournament_id=tournament_id,
            era="v1+v1",
            round_number=1,
            white_key=keys[0],
            black_key=keys[1],
            white_score=1.0,
        )
    )
    await db.commit()
    # ...and the game we are asking about, in another.
    game_id = await _seat_a_tournament_game(
        db, tournament_id, white=keys[0], black=keys[1], era="v3+v4"
    )

    seats = (await client.get(f"/games/{game_id}/event")).json()["seats"]

    assert [seat["played"] for seat in seats] == [0, 0], (
        "a result from era v1+v1 is being counted into a v3+v4 card"
    )


async def test_a_tournament_game_names_its_event(client: AsyncClient, db: AsyncSession) -> None:
    """The left rail's Event card. Without this a reader has a game with no answer to *why was
    this played* — and, more usefully, no way to see which task it ran under."""
    tournament_id, _ = await make_event(db)
    game_id = await _seat_a_tournament_game(db, tournament_id)

    body = (await client.get(f"/games/{game_id}")).json()

    assert body["tournament"] == {
        "slug": "test-cup",
        "name": "Test Cup",
        "format": "round_robin",
        "round_number": 217,
        "era": "v3+v4",
    }


async def test_a_game_nobody_scheduled_names_no_event(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Most games are started by hand. The card is absent, not empty — null rather than a blank
    record the page would have to special-case anyway."""
    from chessmark.orchestration.match import Seat, create_match

    match = await create_match(
        db,
        white=Seat(display_name="w", model="scripted/white"),
        black=Seat(display_name="b", model="scripted/black"),
    )
    await db.commit()

    body = (await client.get(f"/games/{match.game.id}")).json()

    assert body["tournament"] is None


async def test_naming_the_event_costs_one_query(client: AsyncClient, db: AsyncSession) -> None:
    """**A game page is on the critical path of every replay and every live view** (CLAUDE.md,
    *a new read endpoint is measured*).

    The event's name comes along on the join that finds the pairing rather than as a second read,
    and a game with no event pays the same one query to learn so. Counted against the same page
    without an event, so what this forbids is the card costing more than the fact it states.
    """
    tournament_id, _ = await make_event(db)
    with_event = await _seat_a_tournament_game(db, tournament_id)

    from chessmark.orchestration.match import Seat, create_match

    match = await create_match(
        db,
        white=Seat(display_name="w", model="scripted/white"),
        black=Seat(display_name="b", model="scripted/black"),
    )
    await db.commit()

    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        await client.get(f"/games/{match.game.id}")
        plain = len(statements)
        statements.clear()
        await client.get(f"/games/{with_event}")
        evented = len(statements)
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert evented == plain, (
        f"a game in an event took {evented} queries against {plain} for one outside — the card is "
        "reading the tournament separately from the pairing that points at it"
    )

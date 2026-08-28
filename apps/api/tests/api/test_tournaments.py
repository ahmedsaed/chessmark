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
from chessmark.tournament import FieldFilter, Format, TournamentConfig

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

"""The leaderboard endpoint (BENCH-02, BENCH-10).

Two things a public ranking has to do that an internal one does not: print its uncertainty, and
show its exclusions. A rating without a deviation invites comparing three games against three
hundred; a leaderboard that silently drops a third of its games is indistinguishable from a wrong
one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.prompts import PROMPT_VERSION
from chessmark.db.models import ModelEndpoint, ModelRegistry
from chessmark.game import GameResult, Termination
from chessmark.orchestration.match import Seat, create_match

pytestmark = pytest.mark.integration


async def _model(db: AsyncSession, slug: str, quantization: str = "fp8") -> ModelRegistry:
    model = ModelRegistry(
        openrouter_id=slug,
        display_name=slug.split("/")[-1],
        provider=slug.split("/")[0],
        prompt_usd_per_token=Decimal("0.0000001"),
        completion_usd_per_token=Decimal("0.0000004"),
    )
    db.add(model)
    await db.flush()
    db.add(
        ModelEndpoint(
            model_id=model.id,
            provider_name=f"host-{quantization}",
            quantization=quantization,
            uptime_1d=99.0,
        )
    )
    await db.flush()
    return model


async def _played(
    db: AsyncSession,
    white: str,
    black: str,
    *,
    result: GameResult,
    termination: Termination = Termination.CHECKMATE,
    is_ranked: bool = True,
) -> object:
    match = await create_match(
        db,
        white=Seat(display_name=white, model=white),
        black=Seat(display_name=black, model=black),
        is_ranked=is_ranked,
    )
    game = match.game
    game.status = game.status.__class__.FINISHED
    game.result = result
    game.termination = termination
    game.prompt_version = PROMPT_VERSION
    game.ply_count = 40
    await db.commit()
    return game


async def test_an_empty_leaderboard_is_not_an_error(client: AsyncClient) -> None:
    """It is the honest state before any ranked game has been played, and it was the state for the
    whole of Phase 12's development."""
    response = await client.get("/leaderboard")

    assert response.status_code == 200
    assert response.json()["rows"] == []


async def test_a_ranked_game_produces_two_rows(client: AsyncClient, db: AsyncSession) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    body = (await client.get("/leaderboard")).json()

    assert body["games_counted"] == 1
    assert len(body["rows"]) == 2


async def test_the_winner_ranks_above_the_loser(client: AsyncClient, db: AsyncSession) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    rows = (await client.get("/leaderboard")).json()["rows"]

    assert rows[0]["model_slug"] == "test/alpha"
    assert rows[0]["rating"] > rows[1]["rating"]


async def test_every_row_carries_its_uncertainty(client: AsyncClient, db: AsyncSession) -> None:
    """The reason Glicko-2 was chosen over Elo. A rating with no deviation lets a reader compare a
    model with three games against one with three hundred as though they meant the same thing."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    rows = (await client.get("/leaderboard")).json()["rows"]

    assert all(row["rating_deviation"] > 0 for row in rows)


async def test_a_row_carries_the_illegal_move_rate(client: AsyncClient, db: AsyncSession) -> None:
    """The headline number, and the reason the project exists."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    rows = (await client.get("/leaderboard")).json()["rows"]

    assert all("illegal_per_move" in row for row in rows)
    assert all("forfeits" in row for row in rows)


async def test_the_quantization_is_part_of_the_row(client: AsyncClient, db: AsyncSession) -> None:
    """A contestant is `(model, quantization)` (ADR-0015), so the row has to say which."""
    await _model(db, "test/alpha", quantization="fp8")
    await _model(db, "test/beta", quantization="fp4")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    rows = (await client.get("/leaderboard")).json()["rows"]
    by_slug = {row["model_slug"]: row for row in rows}

    assert by_slug["test/alpha"]["quantization"] == "fp8"
    assert by_slug["test/beta"]["quantization"] == "fp4"


async def test_excluded_games_are_listed_with_reasons(
    client: AsyncClient, db: AsyncSession
) -> None:
    """BENCH-10. A methodology page that cannot show its work is asking to be disbelieved."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(
        db,
        "test/alpha",
        "test/beta",
        result=GameResult.DRAW,
        termination=Termination.BUDGET_EXCEEDED,
    )

    body = (await client.get("/leaderboard")).json()

    assert body["games_counted"] == 0
    assert len(body["excluded"]) == 1
    assert "harness" in body["excluded"][0]["reason"]
    assert body["excluded"][0]["game_id"], "an exclusion must name the game so it can be checked"


async def test_an_unranked_game_is_excluded_and_says_so(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS, is_ranked=False)

    body = (await client.get("/leaderboard")).json()

    assert body["rows"] == []
    assert "not a ranked game" in body["excluded"][0]["reason"]


async def test_the_prompt_version_is_reported(client: AsyncClient) -> None:
    """A ranking is only reproducible if it says which task it measured (BENCH-04)."""
    body = (await client.get("/leaderboard")).json()

    assert body["prompt_version"] == PROMPT_VERSION


async def test_the_leaderboard_needs_no_account(client: AsyncClient) -> None:
    """It is the public face of the project (AUTH-02)."""
    assert (await client.get("/leaderboard")).status_code == 200


# ====================================================================== the games behind a row


async def test_a_row_reaches_the_games_it_played_as_white(
    client: AsyncClient, db: AsyncSession
) -> None:
    """**W/D/L said 15; the page listed 8, and the missing seven were every game as white.**

    The drill-down's index recorded each counted game under its *first* seat only, and seats are
    read ordered by colour — so black took every game and white got none. The W/D/L counts come
    from a different pass over the same scan and stayed correct, which is what made this look like
    a display fault rather than a row that could not reach half of its own games (BENCH-02).
    """
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)
    await _played(db, "test/beta", "test/alpha", result=GameResult.WHITE_WINS)

    row = next(
        candidate
        for candidate in (await client.get("/leaderboard")).json()["rows"]
        if candidate["model_slug"] == "test/alpha"
    )
    games = (await client.get("/leaderboard/test/alpha/games")).json()

    assert row["wins"] + row["draws"] + row["losses"] == 2
    assert len(games) == 2, "every game a row counts has to be reachable from it"
    assert {
        player["colour"]
        for game in games
        for player in game["players"]
        if player["model"] == "test/alpha"
    } == {"white", "black"}


async def test_a_mirror_match_is_listed_once(client: AsyncClient, db: AsyncSession) -> None:
    """One contestant holding both seats is still one game.

    The other half of the same bug: the fix indexes every seat, and a model playing itself has two
    of them. Listing it twice would make the page disagree with its row in the opposite direction.
    """
    await _model(db, "test/alpha")
    await _played(
        db,
        "test/alpha",
        "test/alpha",
        result=GameResult.DRAW,
        termination=Termination.STALEMATE,
    )

    games = (await client.get("/leaderboard/test/alpha/games")).json()

    assert len(games) == 1

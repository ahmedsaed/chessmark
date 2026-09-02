"""A rating scoped to one event (ADR-0027).

A pool's table is ordered by a rating computed over that pool's games alone, so a place there
cannot move because of a game played somewhere else — which is the objection to showing the global
number on an event page.

What must **not** change with the scope is eligibility. The narrowing is a `where` clause and
nothing more, so a game the leaderboard excluded is excluded here too and the two can never
disagree about which games count. That is the property most of this file is about.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.bench.service import compute_ratings, ratings_by_key
from chessmark.db import tournaments as repo
from chessmark.db.models import ModelEndpoint, ModelRegistry, TournamentGame
from chessmark.game import GameResult, Termination
from chessmark.orchestration.match import Seat, create_match
from chessmark.tournament import FieldFilter, Format, TournamentConfig

pytestmark = pytest.mark.integration


async def _model(db: AsyncSession, slug: str, quantization: str = "fp8") -> ModelRegistry:
    model = ModelRegistry(
        openrouter_id=slug,
        display_name=slug,
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


async def _event(db: AsyncSession, slug: str) -> uuid.UUID:
    tournament = await repo.create_tournament(
        db,
        name=slug,
        slug=slug,
        config=TournamentConfig(format=Format.POOL, max_concurrent=1, field=FieldFilter()),
        entrants=[],
    )
    created = tournament.id
    await db.flush()
    return created


async def _played(
    db: AsyncSession,
    white: str,
    black: str,
    *,
    result: GameResult,
    termination: Termination = Termination.CHECKMATE,
    event: uuid.UUID | None = None,
    round_number: int = 1,
) -> Any:
    match = await create_match(
        db,
        white=Seat(display_name=white, model=white),
        black=Seat(display_name=black, model=black),
        is_ranked=True,
    )
    game = match.game
    game.status = game.status.__class__.FINISHED
    game.result = result
    game.termination = termination
    game.ply_count = 40
    await db.flush()
    if event is not None:
        db.add(
            TournamentGame(
                tournament_id=event,
                round_number=round_number,
                white_key=white,
                black_key=black,
                game_id=game.id,
                white_score=1.0 if result is GameResult.WHITE_WINS else 0.0,
            )
        )
        await db.flush()
    return game


async def test_a_local_rating_ignores_another_events_games(db: AsyncSession) -> None:
    """The whole point. `alpha` beats `beta` five times in another pool; in *this* one it lost, and
    this pool's table has to say so."""
    for slug in ("test/alpha", "test/beta"):
        await _model(db, slug)
    here, elsewhere = await _event(db, "here"), await _event(db, "elsewhere")

    for round_number in range(1, 6):
        await _played(
            db,
            "test/alpha",
            "test/beta",
            result=GameResult.WHITE_WINS,
            event=elsewhere,
            round_number=round_number,
        )
    await _played(db, "test/beta", "test/alpha", result=GameResult.WHITE_WINS, event=here)

    local = await ratings_by_key(db, tournament_id=here, prompt_version=None)

    assert local["test/beta"][0] > local["test/alpha"][0]

    # And globally the other way, from the same games — which is exactly why the event page must
    # not show the global number.
    everywhere = await compute_ratings(db, prompt_version=None)
    by_label = {c.label: r for c, r in everywhere.ratings.items()}
    assert by_label["test/alpha@fp8"].rating > by_label["test/beta@fp8"].rating


async def test_a_game_outside_any_event_is_not_counted(db: AsyncSession) -> None:
    """A game played by hand, or through the human seat, belongs to no pairing and must not move a
    pool's table."""
    for slug in ("test/alpha", "test/beta"):
        await _model(db, slug)
    here = await _event(db, "here")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS, event=here)
    await _played(db, "test/beta", "test/alpha", result=GameResult.WHITE_WINS, event=None)

    run = await compute_ratings(db, prompt_version=None, tournament_id=here)

    assert run.games_counted == 1


async def test_the_scope_does_not_change_what_counts(db: AsyncSession) -> None:
    """The narrowing is a `where` clause, not a second set of rules. A harness stop is excluded
    here for the same reason and with the same sentence as on the leaderboard."""
    for slug in ("test/alpha", "test/beta"):
        await _model(db, slug)
    here = await _event(db, "here")
    await _played(
        db,
        "test/alpha",
        "test/beta",
        result=GameResult.ONGOING,
        termination=Termination.ABANDONED,
        event=here,
    )

    run = await compute_ratings(db, prompt_version=None, tournament_id=here)

    assert run.games_counted == 0
    assert run.ratings == {}
    assert run.excluded, "an exclusion must still be reported, scoped or not"


async def test_a_model_with_no_ratable_game_is_absent_rather_than_1500(db: AsyncSession) -> None:
    """`ratings_by_key` names only models the games actually measured. The table turns that absence
    into "unrated", which is a different claim from "average" — and the one the deviation exists to
    let us make."""
    for slug in ("test/alpha", "test/beta", "test/never-played"):
        await _model(db, slug)
    here = await _event(db, "here")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS, event=here)

    local = await ratings_by_key(db, tournament_id=here, prompt_version=None)

    assert set(local) == {"test/alpha", "test/beta"}
    assert "test/never-played" not in local

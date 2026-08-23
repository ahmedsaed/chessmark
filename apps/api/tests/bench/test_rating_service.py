"""Rating and aggregating real games.

`glicko2` and `ratable` are tested pure; this is the join to the database. The properties that
matter here are that only eligible games move a rating, that recomputation is deterministic, and
that the exclusions are *reported* rather than silently applied — a methodology page that cannot
show its work is asking to be disbelieved (BENCH-10).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.bench.service import compute_aggregates, compute_ratings, period_of, store_ratings
from chessmark.db.models import ModelEndpoint, ModelRegistry, Rating
from chessmark.game import GameResult, Termination
from chessmark.orchestration.match import Seat, create_match

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


async def _played(
    db: AsyncSession,
    white: str,
    black: str,
    *,
    result: GameResult,
    termination: Termination = Termination.CHECKMATE,
    is_ranked: bool = True,
) -> Any:
    """A finished game, seated through the real match path so routing is pinned as in production."""
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
    game.ply_count = 40
    await db.flush()
    return game


# ====================================================================== eligibility


async def test_only_ranked_games_move_a_rating(db: AsyncSession) -> None:
    """The criterion, asserted directly."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS, is_ranked=False)

    run = await compute_ratings(db, prompt_version=None)

    assert run.games_counted == 0
    assert run.ratings == {}
    assert any("not a ranked game" in e.reason for e in run.excluded)


async def test_a_ranked_game_moves_both_ratings(db: AsyncSession) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    run = await compute_ratings(db, prompt_version=None)

    assert run.games_counted == 1
    by_label = {c.label: r for c, r in run.ratings.items()}
    assert by_label["test/alpha@fp8"].rating > 1500
    assert by_label["test/beta@fp8"].rating < 1500


async def test_a_harness_stop_is_excluded_with_a_reason(db: AsyncSession) -> None:
    """`budget_exceeded` is our budget running out, not a draw either model earned. Two such games
    turned out to be hiding a resignation and a checkmate one ply away."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(
        db,
        "test/alpha",
        "test/beta",
        result=GameResult.DRAW,
        termination=Termination.BUDGET_EXCEEDED,
    )

    run = await compute_ratings(db, prompt_version=None)

    assert run.games_counted == 0
    assert any("harness" in e.reason for e in run.excluded)


async def test_a_forfeit_does_move_the_rating(db: AsyncSession) -> None:
    """Agentic reliability is the measurement. A model that could not operate its tools lost, and
    a leaderboard that hides it measures only chess."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(
        db,
        "test/alpha",
        "test/beta",
        result=GameResult.WHITE_WINS,
        termination=Termination.ILLEGAL_MOVE_FORFEIT,
    )

    run = await compute_ratings(db, prompt_version=None)

    assert run.games_counted == 1


async def test_every_exclusion_carries_a_reason(db: AsyncSession) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.DRAW, is_ranked=False)
    await _played(
        db,
        "test/alpha",
        "test/beta",
        result=GameResult.DRAW,
        termination=Termination.PLY_CAP,
    )

    run = await compute_ratings(db, prompt_version=None)

    assert len(run.excluded) == 2
    assert all(e.reason for e in run.excluded)


# ====================================================================== contestants


async def test_the_same_model_at_two_precisions_is_two_contestants(db: AsyncSession) -> None:
    """ADR-0015 reaching the leaderboard. `model@fp4` and `model@fp8` are separate entrants, and
    averaging them would produce a number describing neither."""
    model = await _model(db, "test/both", quantization="fp8")
    db.add(
        ModelEndpoint(
            model_id=model.id, provider_name="host-fp4", quantization="fp4", uptime_1d=98.0
        )
    )
    await _model(db, "test/rival")
    await db.flush()

    await _played(db, "test/both", "test/rival", result=GameResult.WHITE_WINS)

    run = await compute_ratings(db, prompt_version=None)
    labels = {c.label for c in run.ratings}

    assert "test/both@fp8" in labels, f"expected the pinned precision in the identity, got {labels}"


# ====================================================================== determinism


async def test_recomputing_reproduces_the_same_ratings_exactly(db: AsyncSession) -> None:
    """The determinism criterion. Ratings are a pure function of the games that produced them, and
    a stored value that had drifted from that function would be undetectable."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)
    await _played(db, "test/beta", "test/alpha", result=GameResult.DRAW)

    first = await compute_ratings(db, prompt_version=None)
    second = await compute_ratings(db, prompt_version=None)

    assert {c.label: (r.rating, r.rd, r.volatility) for c, r in first.ratings.items()} == {
        c.label: (r.rating, r.rd, r.volatility) for c, r in second.ratings.items()
    }


async def test_stored_ratings_match_the_computed_run(db: AsyncSession) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    run = await compute_ratings(db, prompt_version=None)
    stored_count = await store_ratings(db, run)

    assert stored_count == len(run.ratings)
    rows = (await db.scalars(sa.select(Rating))).all()
    assert {round(r.rating, 9) for r in rows} == {round(r.rating, 9) for r in run.ratings.values()}


async def test_storing_replaces_rather_than_accumulates(db: AsyncSession) -> None:
    """A row left behind for a contestant that no longer qualifies is a rating nothing supports."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    run = await compute_ratings(db, prompt_version=None)
    await store_ratings(db, run)
    await store_ratings(db, run)

    rows = (await db.scalars(sa.select(Rating))).all()
    assert len(rows) == len(run.ratings)


# ====================================================================== aggregates


async def test_aggregates_cover_the_same_games_as_the_ratings(db: AsyncSession) -> None:
    """A leaderboard whose rating and whose illegal-move rate were computed over different sets of
    games would be quietly incoherent."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)
    await _played(db, "test/alpha", "test/beta", result=GameResult.DRAW, is_ranked=False)

    run = await compute_ratings(db, prompt_version=None)
    aggregates = await compute_aggregates(db, prompt_version=None)

    assert run.games_counted == 1
    assert all(a.games == 1 for a in aggregates.values())


async def test_win_draw_loss_is_counted_per_seat(db: AsyncSession) -> None:
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    aggregates = await compute_aggregates(db, prompt_version=None)
    by_label = {c.label: a for c, a in aggregates.items()}

    assert by_label["test/alpha@fp8"].wins == 1
    assert by_label["test/beta@fp8"].losses == 1


async def test_illegal_rate_is_zero_rather_than_undefined_with_no_moves(
    db: AsyncSession,
) -> None:
    """Dividing by a move count of zero is the obvious crash, and it happens on the first game a
    forfeited model ever plays."""
    await _model(db, "test/alpha")
    await _model(db, "test/beta")
    await _played(db, "test/alpha", "test/beta", result=GameResult.WHITE_WINS)

    aggregates = await compute_aggregates(db, prompt_version=None)

    assert all(a.illegal_per_move == 0.0 for a in aggregates.values())


# ====================================================================== periods


def test_a_period_is_a_day() -> None:
    """Short enough to be responsive, long enough that the deviation can settle — a period holding
    one game would keep every rating maximally uncertain forever."""
    import datetime as dt

    monday = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.UTC)
    later_that_day = dt.datetime(2026, 3, 2, 23, 59, tzinfo=dt.UTC)
    tuesday = dt.datetime(2026, 3, 3, 0, 1, tzinfo=dt.UTC)

    assert period_of(monday) == period_of(later_that_day)
    assert period_of(tuesday) == period_of(monday) + 1

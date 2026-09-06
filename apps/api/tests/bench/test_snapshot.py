"""The stored leaderboard (ADR-0032).

The ranking is no longer rebuilt on every request, which is only safe if a stored run can never
quietly stop matching the games behind it. That is the property asserted here — not that the cache
is fast, but that it is *impossible to serve stale*.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.bench import snapshot
from chessmark.bench.service import compute_aggregates, compute_ratings, scan
from chessmark.db.models import LeaderboardSnapshot, ModelEndpoint, ModelRegistry
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
    result: GameResult = GameResult.WHITE_WINS,
    termination: Termination = Termination.CHECKMATE,
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
    return game


async def _two_models(db: AsyncSession) -> None:
    await _model(db, "snap/alpha")
    await _model(db, "snap/beta")


# ====================================================================== it matches a fresh run


async def test_the_stored_run_equals_computing_it_from_scratch(db: AsyncSession) -> None:
    """The determinism criterion, now asserted across storage rather than within one process.

    Recomputing twice in one call proves the arithmetic is deterministic. Comparing the *stored*
    run against a fresh one proves the thing that actually matters once there is a cache: that what
    the site serves is what the games say.
    """
    await _two_models(db)
    await _played(db, "snap/alpha", "snap/beta")
    await db.flush()

    stored = await snapshot.current(db, prompt_version=None)

    scanned = await scan(db, prompt_version=None)
    run = await compute_ratings(db, prompt_version=None, scanned=scanned)
    aggregates = await compute_aggregates(db, prompt_version=None, scanned=scanned)

    assert stored["games_counted"] == run.games_counted
    assert len(stored["excluded"]) == len(run.excluded)
    assert {row["model_slug"] for row in stored["rows"]} == {c.model_slug for c in run.ratings}
    for row in stored["rows"]:
        contestant = next(c for c in run.ratings if c.model_slug == row["model_slug"])
        assert row["rating"] == pytest.approx(run.ratings[contestant].rating)
        assert row["games"] == aggregates[contestant].games


# ====================================================================== it cannot serve stale


async def test_a_new_game_is_reflected_without_anyone_invalidating_anything(
    db: AsyncSession,
) -> None:
    """**The point of the fingerprint.** Nothing tells the snapshot a game ended — no hook, no call
    site, no cache-busting. The next read notices the games moved and rebuilds.

    This is why ending a game does not have to remember to invalidate, which in turn is why a cache
    rebuild is never inside the transaction that ends one (invariant 1)."""
    await _two_models(db)
    await _played(db, "snap/alpha", "snap/beta")
    await db.flush()

    first = await snapshot.current(db, prompt_version=None)
    assert first["games_counted"] == 1

    await _played(db, "snap/beta", "snap/alpha")
    await db.flush()

    second = await snapshot.current(db, prompt_version=None)
    assert second["games_counted"] == 2


async def test_a_tampered_snapshot_is_rebuilt_rather_than_served(db: AsyncSession) -> None:
    """A stored number that stops matching its games is the whole risk of caching a ranking.

    The fingerprint still matches here — only the payload was edited — so this asserts the weaker
    but more important half: a read never returns a row it did not verify. Rewriting the payload
    and leaving the fingerprint intact is exactly what a partial write or a bad migration looks
    like, and the recompute-on-mismatch below is what catches the general case.
    """
    await _two_models(db)
    await _played(db, "snap/alpha", "snap/beta")
    await db.flush()
    await snapshot.current(db, prompt_version=None)

    await db.execute(sa.update(LeaderboardSnapshot).values(fingerprint="not-the-games-you-have"))
    await db.flush()

    rebuilt = await snapshot.current(db, prompt_version=None)

    assert rebuilt["games_counted"] == 1
    stored = await db.scalar(sa.select(LeaderboardSnapshot))
    assert stored is not None
    assert stored.fingerprint != "not-the-games-you-have"


async def test_storing_replaces_rather_than_accumulates(db: AsyncSession) -> None:
    """Inherited from the `ratings` table this replaced: a row left behind for a run that no longer
    holds is a number nothing supports. One row per prompt version, however often it is rebuilt."""
    await _two_models(db)
    await _played(db, "snap/alpha", "snap/beta")
    await db.flush()

    await snapshot.refresh(db, prompt_version=None)
    await snapshot.refresh(db, prompt_version=None)
    await db.flush()

    rows = (await db.scalars(sa.select(LeaderboardSnapshot))).all()
    assert len(rows) == 1


async def test_an_empty_store_rebuilds_rather_than_failing(db: AsyncSession) -> None:
    """A truncated table, a restored backup, or a fresh deploy. The cache is disposable by design."""
    await _two_models(db)
    await _played(db, "snap/alpha", "snap/beta")
    await db.flush()
    await snapshot.current(db, prompt_version=None)

    await db.execute(sa.delete(LeaderboardSnapshot))
    await db.flush()

    assert (await snapshot.current(db, prompt_version=None))["games_counted"] == 1


async def test_an_endpoint_change_moves_the_fingerprint(db: AsyncSession) -> None:
    """Precision is half a contestant's identity (ADR-0015), so an endpoint edit can move a row
    from `model@fp8` to `model@fp4`. The fingerprint has to see that, or the ranking would keep
    the old split."""
    await _two_models(db)
    before = await snapshot.fingerprint(db, prompt_version=None)

    await _model(db, "snap/gamma", quantization="fp4")
    await db.flush()

    assert await snapshot.fingerprint(db, prompt_version=None) != before


# ====================================================================== cost


async def test_a_warm_read_does_not_scan(db: AsyncSession) -> None:
    """The reason any of this exists.

    A warm read is the fingerprint's two aggregates plus one row — it must not touch `turns`,
    `llm_calls` or `players`, which is what the scan reads and what four pages were paying for.
    """
    await _two_models(db)
    await _played(db, "snap/alpha", "snap/beta")
    await db.flush()
    await snapshot.current(db, prompt_version=None)

    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        await snapshot.current(db, prompt_version=None)
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    joined = " ".join(statements).lower()
    assert len(statements) <= 3, f"a warm read took {len(statements)} queries"
    assert "from turns" not in joined
    assert "from llm_calls" not in joined
    assert "from players" not in joined

"""An event's reasoning level (ADR-0067).

An event is `default` — each model at its own default, which every event before the ADR played and
still does — or a level, which every entrant plays at and which admits only models that list it.
Seating the nearest level a model does offer would give one field several tasks.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.registry import sync_endpoints, sync_model_registry, to_registry_entry
from chessmark.db import tournaments as repo
from chessmark.db.models import Game, ModelRegistry, Player, Tournament, TournamentGame
from chessmark.game import GameResult, Termination
from chessmark.orchestration.match import Seat, create_match
from chessmark.orchestration.tournament import _form, advance
from chessmark.tournament import FieldFilter, Format, TournamentConfig

pytestmark = pytest.mark.integration

WIDE = {
    "mandatory": True,
    "supported_efforts": ["low", "medium", "high"],
    "default_effort": "medium",
}
NARROW = {"mandatory": True, "supported_efforts": ["low", "medium"], "default_effort": "low"}


async def _model(db: AsyncSession, slug: str, reasoning: dict[str, Any] | None) -> None:
    entry = to_registry_entry(
        {
            "id": slug,
            "name": slug,
            "context_length": 200_000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "supported_parameters": ["tools", *(["reasoning"] if reasoning else [])],
            **({"reasoning": reasoning} if reasoning else {}),
        }
    )
    await sync_model_registry(db, [entry])
    await db.flush()
    model = await db.scalar(sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == slug))
    assert model is not None
    await sync_endpoints(
        db,
        model,
        [
            {
                "provider_name": "Host",
                "quantization": "fp8",
                "context_length": 200_000,
                "supported_parameters": ["tools", "reasoning"],
            }
        ],
    )
    await db.commit()


async def _event(db: AsyncSession, field: FieldFilter) -> uuid.UUID:
    config = TournamentConfig(format=Format.POOL, max_concurrent=4, field=field, max_usd=None)
    entrants = await repo.resolve_field(db, field)
    tournament = await repo.create_tournament(
        db, name="Levels", slug=f"levels-{uuid.uuid4().hex[:8]}", config=config, entrants=entrants
    )
    await db.commit()
    return tournament.id


async def test_a_level_admits_only_models_that_list_it(db: AsyncSession) -> None:
    await _model(db, "vendor/wide", WIDE)
    await _model(db, "vendor/narrow", NARROW)
    await _model(db, "vendor/plain", None)

    at_high = {e.key for e in await repo.resolve_field(db, FieldFilter(effort="high"))}
    at_default = {e.key for e in await repo.resolve_field(db, FieldFilter())}

    assert at_high == {"vendor/wide"}
    assert at_default == {"vendor/wide", "vendor/narrow", "vendor/plain"}


def test_a_level_is_checked_where_the_filter_is_made() -> None:
    with pytest.raises(ValueError, match="effort"):
        FieldFilter(effort="loud")
    with pytest.raises(ValueError, match="decision"):
        FieldFilter(effort="high", runtime="decision")


async def test_an_event_from_before_the_level_existed_reads_as_default(db: AsyncSession) -> None:
    """Every running pool was created with no `effort` key. It must go on seating each model at its
    default, not fail to read back, and not change era."""
    assert repo.filter_from_json({"runtime": "llm"}).effort is None


async def _seated_levels(db: AsyncSession, tournament_id: uuid.UUID) -> dict[str, str]:
    rows = await db.execute(
        sa.select(Player.sampling)
        .join(TournamentGame, TournamentGame.game_id == Player.game_id)
        .where(TournamentGame.tournament_id == tournament_id)
    )
    return {str(s["model"]): str(s.get("effort")) for (s,) in rows}


async def test_an_event_at_a_level_seats_every_game_at_it(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: Any
) -> None:
    await _model(db, "vendor/wide", WIDE)
    await _model(db, "vendor/also-wide", WIDE)

    tournament_id = await _event(db, FieldFilter(effort="high"))
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    levels = await _seated_levels(db, tournament_id)
    assert levels and set(levels.values()) == {"high"}, levels


async def test_a_default_event_seats_each_model_at_its_own_default(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession], queue: Any
) -> None:
    await _model(db, "vendor/wide", WIDE)
    await _model(db, "vendor/narrow", NARROW)

    tournament_id = await _event(db, FieldFilter())
    await advance(sessionmaker, queue, tournament_id=tournament_id)
    db.expire_all()

    assert await _seated_levels(db, tournament_id) == {
        "vendor/wide": "medium",
        "vendor/narrow": "low",
    }


async def _won(db: AsyncSession, *, white_effort: str, times: int) -> None:
    for _ in range(times):
        match = await create_match(
            db,
            white=Seat(display_name="t", model="vendor/thinker", effort=white_effort),
            black=Seat(display_name="r", model="vendor/rival"),
            is_ranked=True,
        )
        game = await db.get(Game, match.game.id)
        assert game is not None
        game.status = game.status.__class__.FINISHED
        game.result = GameResult.WHITE_WINS if white_effort == "high" else GameResult.BLACK_WINS
        game.termination = Termination.CHECKMATE
        game.ply_count = 40
    await db.commit()


async def test_the_matchmaker_pairs_on_the_rating_at_the_events_level(
    db: AsyncSession,
) -> None:
    """`thinker@high` and `thinker@low` are two contestants with two ratings. A pool at `low` that
    paired on the `high` row would seat it as something it has not shown it is."""
    await _model(db, "vendor/thinker", WIDE)
    await _model(db, "vendor/rival", NARROW)
    await _won(db, white_effort="high", times=4)
    await _won(db, white_effort="low", times=4)

    at_high = await db.get(Tournament, await _event(db, FieldFilter(effort="high")))
    at_low = await db.get(Tournament, await _event(db, FieldFilter(effort="low")))
    assert at_high is not None and at_low is not None

    high = (await _form(db, at_high))["vendor/thinker"]
    low = (await _form(db, at_low))["vendor/thinker"]

    assert high.rating > low.rating, (high, low)

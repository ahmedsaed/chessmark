"""Seating a match.

The interesting part is the two ways a player records which model it is. `sampling["model"]` holds
the slug the game actually ran and must survive a rename; `model_id` is the foreign key aggregate
queries join on. Both are needed, and the second one was silently never set.
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import PlayerKind
from chessmark.db.models import ModelRegistry, Player
from chessmark.orchestration.match import Seat, create_match, registry_id_for


async def _register(db: AsyncSession, slug: str) -> ModelRegistry:
    model = ModelRegistry(
        openrouter_id=slug,
        display_name=slug,
        provider=slug.split("/")[0],
        context_length=128_000,
        prompt_usd_per_token=Decimal("0.0000001"),
        completion_usd_per_token=Decimal("0.0000004"),
    )
    db.add(model)
    await db.flush()
    return model


async def test_a_seat_links_to_its_registry_row(db: AsyncSession) -> None:
    """Without this the leaderboard cannot see the game at all: ratings group by `model_id`, and
    a NULL there drops the result on the floor rather than failing loudly."""
    white = await _register(db, "google/gemini-2.5-flash-lite")
    black = await _register(db, "deepseek/deepseek-v4-flash")

    match = await create_match(
        db,
        white=Seat(display_name="gemini", model=white.openrouter_id),
        black=Seat(display_name="deepseek", model=black.openrouter_id),
    )

    assert match.white.model_id == white.id
    assert match.black.model_id == black.id


async def test_the_slug_is_still_recorded_on_the_player(db: AsyncSession) -> None:
    """The FK is an addition, not a replacement. A renamed registry row must not rewrite what a
    finished game says it ran."""
    model = await _register(db, "deepseek/deepseek-v4-flash")

    match = await create_match(
        db,
        white=Seat(display_name="deepseek", model=model.openrouter_id),
        black=Seat(display_name="deepseek", model=model.openrouter_id),
    )

    assert match.white.sampling["model"] == "deepseek/deepseek-v4-flash"


async def test_an_unregistered_model_is_still_playable(db: AsyncSession) -> None:
    """`scripted/white` is not a real model and never will be. An unknown slug must seat normally
    and simply not aggregate — raising here would make the whole test suite unable to start a
    game."""
    match = await create_match(
        db,
        white=Seat(display_name="white", model="scripted/white"),
        black=Seat(display_name="black", model="scripted/black"),
    )

    assert match.white.model_id is None
    assert match.white.sampling["model"] == "scripted/white"


async def test_an_explicit_model_id_wins_over_the_slug(db: AsyncSession) -> None:
    """A caller that already resolved the row is not second-guessed."""
    registered = await _register(db, "google/gemini-2.5-flash-lite")
    other = await _register(db, "openai/gpt-5-nano")

    match = await create_match(
        db,
        white=Seat(display_name="pinned", model=registered.openrouter_id, model_id=other.id),
        black=Seat(display_name="black", model=registered.openrouter_id),
    )

    assert match.white.model_id == other.id


async def test_the_link_survives_the_transaction(db: AsyncSession) -> None:
    """Set on the ORM object is not the same as stored. Read it back from the database."""
    model = await _register(db, "deepseek/deepseek-v4-flash")
    match = await create_match(
        db,
        white=Seat(display_name="deepseek", model=model.openrouter_id),
        black=Seat(display_name="deepseek", model=model.openrouter_id),
    )
    await db.flush()

    stored = await db.scalar(sa.select(Player.model_id).where(Player.id == match.white.id))

    assert stored == model.id


async def test_an_unknown_slug_resolves_to_nothing(db: AsyncSession) -> None:
    assert await registry_id_for(db, "nobody/nothing") is None
    assert await registry_id_for(db, None) is None
    assert await registry_id_for(db, "") is None


async def test_resolution_is_by_exact_slug(db: AsyncSession) -> None:
    """A prefix or case-variant must not silently attach a game to the wrong model."""
    model = await _register(db, "deepseek/deepseek-v4-flash")

    assert await registry_id_for(db, "deepseek/deepseek-v4-flash") == model.id
    assert await registry_id_for(db, "deepseek/deepseek-v4") is None
    assert await registry_id_for(db, "DeepSeek/DeepSeek-V4-Flash") is None


async def test_a_human_seat_has_no_model(db: AsyncSession) -> None:
    """A human plays no model, so there is no slug to resolve and nothing to link."""
    match = await create_match(
        db,
        white=Seat(display_name="ahmed", kind=PlayerKind.HUMAN),
        black=Seat(display_name="model", model="scripted/black"),
    )

    assert match.white.model_id is None
    assert match.white.sampling == {}

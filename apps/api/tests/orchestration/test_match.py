"""Seating a match.

The interesting part is the two ways a player records which model it is. `sampling["model"]` holds
the slug the game actually ran and must survive a rename; `model_id` is the foreign key aggregate
queries join on. Both are needed, and the second one was silently never set.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
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


# ====================================================================== endpoint pinning


async def _with_endpoints(db: AsyncSession, slug: str, endpoints: list[dict]) -> None:
    from chessmark.db.models import ModelEndpoint

    model = await _register(db, slug)
    for endpoint in endpoints:
        db.add(
            ModelEndpoint(
                model_id=model.id,
                provider_name=endpoint["provider"],
                quantization=endpoint.get("quantization"),
                uptime_1d=endpoint.get("uptime"),
            )
        )
    await db.flush()


async def test_a_seat_pins_exactly_one_endpoint(db: AsyncSession) -> None:
    """ADR-0015. The first paid benchmark was served by Baidu for 70 calls and StreamLake for 33
    inside one game — a blend nothing can reproduce, and the two are not equivalent."""
    await _with_endpoints(
        db,
        "test/pinned",
        [
            {"provider": "Solid", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Flaky", "quantization": "fp8", "uptime": 80.0},
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/pinned"),
        black=Seat(display_name="b", model="test/pinned"),
    )

    assert match.white.provider_routing["only"] == ["Solid"]
    assert match.black.provider_routing["only"] == ["Solid"]


async def test_pinning_clears_the_precision_filter(db: AsyncSession) -> None:
    """The endpoint *is* the constraint once it is chosen. Naming a precision as well would refuse
    the very endpoint just selected whenever it reports `unknown`."""
    await _with_endpoints(
        db, "test/closed", [{"provider": "Vendor", "quantization": "unknown", "uptime": 99.0}]
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/closed"),
        black=Seat(display_name="b", model="test/closed"),
    )

    assert match.white.provider_routing["only"] == ["Vendor"]
    assert not match.white.provider_routing.get("quantizations")


async def test_a_seat_can_ask_for_a_precision(db: AsyncSession) -> None:
    """`model@fp4` is a contestant. Asking for it pins an fp4 endpoint even though a healthier fp8
    one exists, because they are different contestants and must not be averaged."""
    await _with_endpoints(
        db,
        "test/both",
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Four", "quantization": "fp4", "uptime": 70.0},
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/both", quantization="fp4"),
        black=Seat(display_name="b", model="test/both", quantization="fp8"),
    )

    assert match.white.provider_routing["only"] == ["Four"]
    assert match.black.provider_routing["only"] == ["Eight"]


async def test_a_seat_can_force_an_endpoint(db: AsyncSession) -> None:
    """For telling a model's fault apart from its host's — the investigation that found
    StreamLake mangling tool calls needed exactly this."""
    await _with_endpoints(
        db,
        "test/forced",
        [
            {"provider": "Healthy", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Suspect", "quantization": "fp8", "uptime": 99.0},
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/forced", provider="Suspect"),
        black=Seat(display_name="b", model="test/forced"),
    )

    assert match.white.provider_routing["only"] == ["Suspect"]
    assert match.black.provider_routing["only"] == ["Healthy"]


async def test_asking_for_an_unserved_precision_refuses_the_match(db: AsyncSession) -> None:
    """Rather than quietly seating a different contestant."""
    from chessmark.agents.registry import NoEndpointError

    await _with_endpoints(
        db, "test/eightonly", [{"provider": "Eight", "quantization": "fp8", "uptime": 99.0}]
    )

    with pytest.raises(NoEndpointError):
        await create_match(
            db,
            white=Seat(display_name="w", model="test/eightonly", quantization="fp4"),
            black=Seat(display_name="b", model="test/eightonly"),
        )


async def test_a_model_with_no_synced_endpoints_still_plays(db: AsyncSession) -> None:
    """Better a game that runs and records what served it than a refusal over missing bookkeeping.
    `scripted/white` will never have an endpoint row."""
    match = await create_match(
        db,
        white=Seat(display_name="w", model="scripted/white"),
        black=Seat(display_name="b", model="scripted/black"),
    )

    assert not match.white.provider_routing.get("only")

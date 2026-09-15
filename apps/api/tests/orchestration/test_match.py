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

from chessmark.agents.routing import DEFAULT_QUANTIZATIONS
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
        is_ranked=True,
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
        is_ranked=True,
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
        is_ranked=True,
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


async def test_the_game_records_the_precision_its_seats_actually_ran_at(db: AsyncSession) -> None:
    """Invariant 3: the record has to be true.

    The game's blob was written before the seats resolved, so it carried the *request* — a default
    policy of fp8-and-above — while `resolve_routing` pinned endpoints that serve 4-bit. In
    production 15 seats across 14 games ran at `nvfp4` or `fp4` under a game record saying they
    could not have. Nothing was mis-rated (a contestant is `(model, quantization)`), but a reader
    asking the game what it ran under got the wrong answer.
    """
    await _with_endpoints(
        db, "test/lowbit", [{"provider": "Cheap", "quantization": "fp4", "uptime": 99.0}]
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/lowbit", quantization="fp4"),
        black=Seat(display_name="b", model="test/lowbit", quantization="fp4"),
    )

    assert match.game.provider_routing is not None
    assert match.game.provider_routing["quantizations"] == [], (
        "the seats are pinned to one endpoint each, so the game is bound by no precision filter — "
        f"it claims {match.game.provider_routing['quantizations']}"
    )


async def test_an_unresolved_seat_keeps_its_policy_on_the_game(db: AsyncSession) -> None:
    """The other direction. A model with no endpoint rows is never pinned, so the requested filter
    is still what binds it and the game must go on saying so."""
    match = await create_match(
        db,
        white=Seat(display_name="w", model="scripted/white"),
        black=Seat(display_name="b", model="scripted/black"),
    )

    assert match.game.provider_routing is not None
    assert match.game.provider_routing["quantizations"] == list(DEFAULT_QUANTIZATIONS)


# ================================================= only a rated game is pinned (ADR-0044)


async def test_an_unranked_seat_keeps_every_endpoint(db: AsyncSession) -> None:
    """**A pin buys reproducibility and costs every other endpoint the model has.**

    `deepseek-v4.1-flash` has eighteen. An exhibition game pinned to the one BaseTen was throttling
    spent its life climbing the cooldown ladder — 60s, 300s, 900s — while seventeen others were
    answering. A game nobody will rate gains nothing from the first and pays all of the second.
    """
    await _with_endpoints(
        db,
        "test/many",
        [
            {"provider": "Hot", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Spare", "quantization": "fp8", "uptime": 99.0},
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/many"),
        black=Seat(display_name="b", model="test/many"),
        is_ranked=False,
    )

    assert not match.white.provider_routing.get("only"), (
        "an unranked seat was pinned to one endpoint and cannot route around it"
    )
    assert not match.black.provider_routing.get("only")


async def test_an_unranked_seat_that_names_a_provider_still_gets_it(db: AsyncSession) -> None:
    """An explicit provider is an instruction, not a policy — it is what tells a model's fault
    apart from its host's, and that investigation is never a rated game."""
    await _with_endpoints(
        db,
        "test/named",
        [
            {"provider": "Healthy", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Suspect", "quantization": "fp8", "uptime": 99.0},
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/named", provider="Suspect"),
        black=Seat(display_name="b", model="test/named"),
        is_ranked=False,
    )

    assert match.white.provider_routing["only"] == ["Suspect"]
    assert not match.black.provider_routing.get("only")


async def test_an_unranked_seat_that_asks_for_a_precision_is_pinned(db: AsyncSession) -> None:
    """The precision lives on the endpoint, so honouring the request means pinning. Leaving the
    router free would seat `model@fp8` for a caller who asked for `model@fp4` — different
    contestants (ADR-0015), and the one thing an unpinned seat must still not do."""
    await _with_endpoints(
        db,
        "test/precise",
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Four", "quantization": "fp4", "uptime": 70.0},
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/precise", quantization="fp4"),
        black=Seat(display_name="b", model="test/precise"),
        is_ranked=False,
    )

    assert match.white.provider_routing["only"] == ["Four"]
    assert not match.black.provider_routing.get("only")


async def test_an_unranked_seat_still_refuses_a_precision_nothing_serves(db: AsyncSession) -> None:
    """Checked before the pin is, so going unpinned cannot turn a caller's mistake into a quietly
    different contestant."""
    from chessmark.agents.registry import NoEndpointError

    await _with_endpoints(
        db, "test/eightonly2", [{"provider": "Eight", "quantization": "fp8", "uptime": 99.0}]
    )

    with pytest.raises(NoEndpointError):
        await create_match(
            db,
            white=Seat(display_name="w", model="test/eightonly2", quantization="fp4"),
            black=Seat(display_name="b", model="test/eightonly2"),
            is_ranked=False,
        )

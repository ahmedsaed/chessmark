"""A seat's reasoning level: settled, recorded, refused, and labelled (ADR-0067).

Before this, no request carried a level and no seat recorded one, so every model reasoned at its
provider's default and a rating could move when that default did. These pin the four things that
make it a contestant's identity rather than a setting: it is settled when the game is created, it
is recorded on the seat, a level the model does not list is refused, and seats from before are
labelled from what they actually did.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import (
    NoEndpointError,
    sync_endpoints,
    sync_model_registry,
    to_registry_entry,
)
from chessmark.db.effort_labels import label_unlabelled_seats
from chessmark.db.enums import PlayerKind
from chessmark.db.models import (
    LeaderboardSnapshot,
    LlmCall,
    ModelEndpoint,
    ModelRegistry,
    Player,
    Turn,
)
from chessmark.orchestration.match import Seat, UnavailableEffortError, create_match

pytestmark = pytest.mark.integration

#: GPT-6.1 Sol's block as OpenRouter served it: always on, five efforts, medium by default.
ALWAYS_ON = {
    "mandatory": True,
    "default_enabled": True,
    "supported_efforts": ["max", "xhigh", "high", "medium", "low"],
    "default_effort": "medium",
}
#: Optional and off by default, but naming an effort for when it is switched on.
OPTIONAL = {
    "mandatory": False,
    "default_enabled": False,
    "supported_efforts": ["high", "medium", "low"],
    "default_effort": "medium",
}


async def _model(
    db: AsyncSession,
    slug: str,
    reasoning: dict[str, Any] | None,
    endpoints: list[dict[str, Any]] | None = None,
) -> ModelRegistry:
    """Register a model the way the catalogue refresh does, from an OpenRouter-shaped entry."""
    entry = to_registry_entry(
        {
            "id": slug,
            "name": slug,
            "context_length": 128_000,
            "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
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
        endpoints
        or [
            {
                "provider_name": "Host",
                "quantization": "fp8",
                "supported_parameters": ["tools", "reasoning"],
                "uptime_last_1d": 99.0,
            }
        ],
    )
    return model


# ====================================================================== the catalogue


async def test_the_catalogue_gives_a_model_its_levels(db: AsyncSession) -> None:
    model = await _model(db, "test/always-on", ALWAYS_ON)

    assert model.reasoning == ALWAYS_ON, "the block is kept verbatim, for tracing a level back"
    assert model.reasoning_levels == ["low", "medium", "high", "xhigh", "max"]
    assert model.default_reasoning == "medium"


async def test_an_endpoint_records_whether_it_takes_a_level(db: AsyncSession) -> None:
    model = await _model(
        db,
        "test/mixed-hosts",
        ALWAYS_ON,
        [
            {"provider_name": "Takes", "supported_parameters": ["tools", "reasoning"]},
            {"provider_name": "Ignores", "supported_parameters": ["tools"]},
        ],
    )
    rows = {
        e.provider_name: e.supports_reasoning
        for e in await db.scalars(
            sa.select(ModelEndpoint).where(ModelEndpoint.model_id == model.id)
        )
    }
    assert rows == {"Takes": True, "Ignores": False}


# ====================================================================== the seat


async def test_a_seat_that_names_no_level_records_the_default(db: AsyncSession) -> None:
    """Sent explicitly and recorded, so the record says what was asked for even when nobody chose
    it — and a default that moves later cannot change what this game was."""
    await _model(db, "test/defaulted", ALWAYS_ON)

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/defaulted"),
        black=Seat(display_name="b", model="test/defaulted", effort="high"),
    )

    assert match.white.sampling["effort"] == "medium"
    assert match.black.sampling["effort"] == "high"
    assert "effort_inferred" not in match.white.sampling


async def test_a_level_the_model_does_not_list_is_refused(db: AsyncSession) -> None:
    """Not rounded to the nearest: `none` on an always-on model would be a different contestant,
    and the provider would refuse or ignore it anyway."""
    await _model(db, "test/no-off-switch", ALWAYS_ON)

    with pytest.raises(UnavailableEffortError, match="none"):
        await create_match(
            db,
            white=Seat(display_name="w", model="test/no-off-switch", effort="none"),
            black=Seat(display_name="b", model="test/no-off-switch"),
        )


async def test_none_is_a_level_where_reasoning_is_optional(db: AsyncSession) -> None:
    await _model(db, "test/optional", OPTIONAL)

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/optional"),
        black=Seat(display_name="b", model="test/optional", effort="low"),
    )

    assert match.white.sampling["effort"] == "none", "off by default, so off unless asked"
    assert match.black.sampling["effort"] == "low"


async def test_a_seat_with_a_level_is_not_pinned_to_an_endpoint_that_ignores_it(
    db: AsyncSession,
) -> None:
    """The healthier host would have played at whatever its provider defaults to, while the record
    said `high`."""
    await _model(
        db,
        "test/pick-the-one-that-listens",
        ALWAYS_ON,
        [
            {
                "provider_name": "Ignores",
                "supported_parameters": ["tools"],
                "uptime_last_1d": 99.9,
            },
            {
                "provider_name": "Takes",
                "supported_parameters": ["tools", "reasoning"],
                "uptime_last_1d": 90.0,
            },
        ],
    )

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/pick-the-one-that-listens", effort="high"),
        black=Seat(display_name="b", model="test/pick-the-one-that-listens"),
    )

    assert match.white.provider_routing["only"] == ["Takes"]
    assert match.black.provider_routing["only"] == ["Takes"]

    with pytest.raises(NoEndpointError, match="reasoning"):
        await create_match(
            db,
            white=Seat(
                display_name="w", model="test/pick-the-one-that-listens", provider="Ignores"
            ),
            black=Seat(display_name="b", model="test/pick-the-one-that-listens"),
        )


async def test_a_model_the_catalogue_has_not_described_sends_nothing(db: AsyncSession) -> None:
    """The minutes between the migration and the refresh: such a seat plays as every seat did
    before, with no level, and the refresh labels it afterwards."""
    db.add(
        ModelRegistry(
            openrouter_id="test/undescribed",
            display_name="undescribed",
            provider="test",
            context_length=128_000,
            prompt_usd_per_token=Decimal("0.0000001"),
            completion_usd_per_token=Decimal("0.0000004"),
        )
    )
    await db.flush()

    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/undescribed"),
        black=Seat(display_name="b", model="test/undescribed"),
    )
    assert "effort" not in match.white.sampling

    with pytest.raises(UnavailableEffortError):
        await create_match(
            db,
            white=Seat(display_name="w", model="test/undescribed", effort="high"),
            black=Seat(display_name="b", model="test/undescribed"),
        )


# ====================================================================== labelling the past


async def _old_seat(db: AsyncSession, slug: str, *, reasoning_tokens: int) -> Player:
    """A seat as every game before ADR-0067 left it: no level, and one call."""
    match = await create_match(
        db,
        white=Seat(display_name="w", model=slug),
        black=Seat(display_name="b", model=slug),
    )
    player = match.white
    player.sampling = {"model": slug}
    turn = Turn(game_id=match.game.id, player_id=player.id)
    db.add(turn)
    await db.flush()
    db.add(
        LlmCall(
            game_id=match.game.id,
            turn_id=turn.id,
            sequence=1,
            model_slug=slug,
            request={},
            response={},
            prompt_tokens=10,
            completion_tokens=10 + reasoning_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_usd=Decimal(0),
        )
    )
    await db.flush()
    return player


async def test_an_old_seat_is_labelled_from_whether_it_reasoned(db: AsyncSession) -> None:
    """The tokens were recorded at the time; the level's *name* comes from today's catalogue. A
    seat on an optional model that reasoned was reasoning at the effort the model uses when on,
    whatever the catalogue now says its default is."""
    await _model(db, "test/old-optional", OPTIONAL)
    thought = await _old_seat(db, "test/old-optional", reasoning_tokens=400)
    silent = await _old_seat(db, "test/old-optional", reasoning_tokens=0)
    db.add(LeaderboardSnapshot(prompt_version="v", scope="", fingerprint="f", payload={}))
    await db.flush()

    report = await label_unlabelled_seats(db)

    assert thought.sampling == {
        "model": "test/old-optional",
        "effort": "medium",
        "effort_inferred": True,
    }
    assert silent.sampling["effort"] == "none"
    assert report.labelled == 2
    assert await db.scalar(sa.select(sa.func.count(LeaderboardSnapshot.id))) == 0, (
        "a stored run keyed by the old contestants would go on being served: its fingerprint is "
        "over the games, and they did not change"
    )


async def test_labelling_touches_only_seats_with_no_level(db: AsyncSession) -> None:
    await _model(db, "test/already-labelled", ALWAYS_ON)
    match = await create_match(
        db,
        white=Seat(display_name="w", model="test/already-labelled", effort="high"),
        black=Seat(display_name="b", model="test/already-labelled"),
    )

    report = await label_unlabelled_seats(db)

    assert report.labelled == 0
    assert match.white.sampling["effort"] == "high"


async def test_a_person_is_never_labelled(db: AsyncSession) -> None:
    await _model(db, "test/opponent", ALWAYS_ON)
    match = await create_match(
        db,
        white=Seat(display_name="you", kind=PlayerKind.HUMAN),
        black=Seat(display_name="b", model="test/opponent"),
    )
    match.black.sampling = {"model": "test/opponent"}
    await db.flush()

    await label_unlabelled_seats(db)

    assert "effort" not in (match.white.sampling or {})
    assert match.black.sampling["effort"] == "medium"

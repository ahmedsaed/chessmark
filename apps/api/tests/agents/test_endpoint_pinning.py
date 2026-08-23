"""Endpoint selection and pinning (ADR-0015).

The bug this exists to prevent has already happened: the first paid benchmark, quoted repeatedly as
a result about `deepseek-v4-flash`, was served by **Baidu for 70 calls and StreamLake for 33 inside
one game**. That number measures a blend the router chose, and re-running it would choose a
different one. Worse, the two endpoints are not equivalent — one of them mangles tool calls.

So a seat resolves to exactly one endpoint, before the game starts, and keeps it.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import (
    NoEndpointError,
    is_floating_alias,
    quantizations_offered,
    select_endpoint,
    sync_model_registry,
)
from chessmark.db.models import ModelEndpoint, ModelRegistry

pytestmark = pytest.mark.integration

SLUG = "test/multi-endpoint"


async def _seed(db: AsyncSession, endpoints: list[dict]) -> None:
    await sync_model_registry(db, [{"openrouter_id": SLUG, "display_name": "Multi"}])
    await db.flush()
    model_id = await db.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == SLUG)
    )
    for endpoint in endpoints:
        db.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name=endpoint["provider"],
                quantization=endpoint.get("quantization"),
                supports_tools=endpoint.get("supports_tools", True),
                is_active=endpoint.get("is_active", True),
                uptime_1d=endpoint.get("uptime"),
                throughput=endpoint.get("throughput"),
            )
        )
    await db.flush()


# ====================================================================== selection


async def test_the_healthiest_endpoint_wins(db: AsyncSession) -> None:
    """Uptime, highest first. OpenRouter exposes no request counts, and uptime measures the
    property we actually want — "most likely to behave" — more directly than popularity would."""
    await _seed(
        db,
        [
            {"provider": "Flaky", "quantization": "fp8", "uptime": 91.0},
            {"provider": "Solid", "quantization": "fp8", "uptime": 99.5},
            {"provider": "Middling", "quantization": "fp8", "uptime": 96.0},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Solid"


async def test_throughput_breaks_a_tie(db: AsyncSession) -> None:
    await _seed(
        db,
        [
            {"provider": "Slow", "quantization": "fp8", "uptime": 99.0, "throughput": 10.0},
            {"provider": "Fast", "quantization": "fp8", "uptime": 99.0, "throughput": 90.0},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Fast"


async def test_an_unmeasured_endpoint_loses_to_a_measured_one(db: AsyncSession) -> None:
    """Never having been measured is not a recommendation — but it beats having no endpoint."""
    await _seed(
        db,
        [
            {"provider": "Unknown", "quantization": "fp8", "uptime": None},
            {"provider": "Known", "quantization": "fp8", "uptime": 80.0},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Known"


async def test_an_endpoint_without_tools_is_never_selected(db: AsyncSession) -> None:
    """An agent that cannot act cannot play (AGENT-01). Choosing one would produce a forfeit that
    says nothing about the model."""
    await _seed(
        db,
        [
            {
                "provider": "NoTools",
                "quantization": "fp8",
                "uptime": 100.0,
                "supports_tools": False,
            },
            {"provider": "Tools", "quantization": "fp8", "uptime": 50.0},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Tools"


async def test_an_inactive_endpoint_is_never_selected(db: AsyncSession) -> None:
    await _seed(
        db,
        [
            {"provider": "Gone", "quantization": "fp8", "uptime": 100.0, "is_active": False},
            {"provider": "Here", "quantization": "fp8", "uptime": 50.0},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Here"


# ====================================================================== quantization as identity


async def test_a_precision_is_asked_for_not_filtered_out(db: AsyncSession) -> None:
    """ADR-0015's reversal. `model@fp4` is a contestant, not a violation — asking for fp4 pins an
    fp4 endpoint even when a healthier fp8 one exists, because they are different contestants."""
    await _seed(
        db,
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Four", "quantization": "fp4", "uptime": 70.0},
        ],
    )

    chosen = await select_endpoint(db, model_slug=SLUG, quantization="fp4")

    assert chosen.provider_name == "Four"
    assert chosen.quantization == "fp4"


async def test_the_healthiest_fp4_endpoint_wins_among_fp4(db: AsyncSession) -> None:
    await _seed(
        db,
        [
            {"provider": "BadFour", "quantization": "fp4", "uptime": 60.0},
            {"provider": "GoodFour", "quantization": "fp4", "uptime": 98.0},
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.9},
        ],
    )

    assert (
        await select_endpoint(db, model_slug=SLUG, quantization="fp4")
    ).provider_name == "GoodFour"


async def test_asking_for_a_precision_nobody_serves_is_an_error(db: AsyncSession) -> None:
    """Not a silent fallback. A caller who asked for fp4 and got fp8 would be measuring the wrong
    contestant and would have no way to know."""
    await _seed(db, [{"provider": "Eight", "quantization": "fp8", "uptime": 99.0}])

    with pytest.raises(NoEndpointError, match="fp4"):
        await select_endpoint(db, model_slug=SLUG, quantization="fp4")


async def test_undeclared_precision_is_playable(db: AsyncSession) -> None:
    """ADR-0014 refused `unknown`. ADR-0015 records it instead: a closed-weight model has nothing
    to disclose, and excluding it selects for open weights rather than for measurement quality."""
    await _seed(db, [{"provider": "Closed", "quantization": "unknown", "uptime": 99.0}])

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Closed"


async def test_every_precision_offered_is_listed(db: AsyncSession) -> None:
    """One contestant per entry."""
    await _seed(
        db,
        [
            {"provider": "A", "quantization": "fp8", "uptime": 99.0},
            {"provider": "B", "quantization": "fp4", "uptime": 99.0},
            {"provider": "C", "quantization": "fp8", "uptime": 99.0},
            {"provider": "D", "quantization": None, "uptime": 99.0},
        ],
    )

    assert await quantizations_offered(db, SLUG) == ["fp4", "fp8", "unknown"]


async def test_an_unknown_model_has_no_endpoint(db: AsyncSession) -> None:
    with pytest.raises(NoEndpointError):
        await select_endpoint(db, model_slug="nobody/nothing")


# ====================================================================== floating aliases


@pytest.mark.parametrize(
    "slug",
    ["~deepseek/deepseek-v4-flash-latest", "~google/gemini-flash-latest", "vendor/model-latest"],
)
def test_a_floating_alias_is_recognised(slug: str) -> None:
    """It points at different weights over time, so a rating computed across it rates nothing."""
    assert is_floating_alias(slug)


@pytest.mark.parametrize(
    "slug",
    ["deepseek/deepseek-v4-flash", "google/gemini-3.7-flash", "openai/gpt-5.4-mini"],
)
def test_a_pinned_version_is_not_a_floating_alias(slug: str) -> None:
    assert not is_floating_alias(slug)


# ====================================================================== declared beats unknown


async def test_a_declared_precision_beats_unknown_even_at_lower_uptime(
    db: AsyncSession,
) -> None:
    """The real case, from `z-ai/glm-4.7`.

    Uptime alone picked Google Vertex at `unknown` (99.98%) over Novita at fp8 (95.93%) — recorded
    honestly, but a reseller at an undeclared precision is not what anyone means by "GLM-4.7".
    `unknown` says the least about what actually ran, so it is a contestant you may ask for and no
    longer the silent default.
    """
    await _seed(
        db,
        [
            {"provider": "Google", "quantization": "unknown", "uptime": 99.98},
            {"provider": "Novita", "quantization": "fp8", "uptime": 95.93},
            {"provider": "Z.AI", "quantization": "fp4", "uptime": 95.61},
        ],
    )

    chosen = await select_endpoint(db, model_slug=SLUG)

    assert chosen.provider_name == "Novita"
    assert chosen.quantization == "fp8"


async def test_unknown_still_wins_when_it_is_all_there_is(db: AsyncSession) -> None:
    """A closed-weight model has nothing to declare. Preferring declared precision must not make
    it unplayable — that would be ADR-0014's exclusion policy returning by the back door."""
    await _seed(
        db,
        [
            {"provider": "Google", "quantization": "unknown", "uptime": 99.0},
            {"provider": "Azure", "quantization": None, "uptime": 99.9},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Azure"


async def test_unknown_can_still_be_asked_for_by_name(db: AsyncSession) -> None:
    """It is a contestant, not a fallback. Asking for it pins it even against a declared one."""
    await _seed(
        db,
        [
            {"provider": "Reseller", "quantization": "unknown", "uptime": 99.9},
            {"provider": "Specialist", "quantization": "fp8", "uptime": 99.0},
        ],
    )

    chosen = await select_endpoint(db, model_slug=SLUG, quantization="unknown")

    assert chosen.provider_name == "Reseller"


async def test_uptime_still_decides_between_two_declared_precisions(db: AsyncSession) -> None:
    """The preference is declared-over-unknown, not fp8-over-fp4. Both are real contestants and
    neither is inherently the right default."""
    await _seed(
        db,
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 90.0},
            {"provider": "Four", "quantization": "fp4", "uptime": 99.0},
        ],
    )

    assert (await select_endpoint(db, model_slug=SLUG)).provider_name == "Four"

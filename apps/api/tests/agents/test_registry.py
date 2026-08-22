"""Model registry sync."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import (
    load_pricing_table,
    load_seed,
    playable_models,
    provider_of,
    sync_model_registry,
)


def test_provider_is_the_vendor_half_of_the_slug() -> None:
    assert provider_of("nvidia/nemotron-nano-9b-v2:free") == "nvidia"
    assert provider_of("openai/gpt-oss-20b:free") == "openai"
    assert provider_of("bare-name") == "unknown"


def test_the_seed_file_is_readable_and_tool_capable() -> None:
    """AGENT-01: a model that cannot call tools cannot play, so none should be seeded."""
    entries = load_seed()

    assert len(entries) >= 10
    assert all(entry["openrouter_id"] for entry in entries)
    assert all(entry.get("supports_tools", True) for entry in entries)


@pytest.mark.integration
async def test_sync_creates_then_is_idempotent(db: AsyncSession) -> None:
    entries = load_seed()

    first = await sync_model_registry(db, entries)
    await db.commit()

    assert len(first.created) == len(entries)
    assert first.updated == []

    second = await sync_model_registry(db, entries)
    await db.commit()

    assert second.created == []
    assert second.updated == [], "a second sync of unchanged data must be a no-op"


@pytest.mark.integration
async def test_sync_updates_changed_pricing(db: AsyncSession) -> None:
    """Stale pricing means wrong budget caps (ADR-0011), so changes must land."""
    entries = [
        {
            "openrouter_id": "test/model",
            "display_name": "Test",
            "prompt_usd_per_token": 0.000001,
            "completion_usd_per_token": 0.000002,
            "context_length": 1000,
        }
    ]
    await sync_model_registry(db, entries)
    await db.commit()

    entries[0]["prompt_usd_per_token"] = 0.000009
    report = await sync_model_registry(db, entries)
    await db.commit()

    assert report.updated == ["test/model"]

    table = await load_pricing_table(db)
    pricing = table.get("test/model")
    assert pricing is not None
    assert pricing.prompt_usd_per_token == Decimal("0.000009")


@pytest.mark.integration
async def test_missing_models_are_disabled_not_deleted(db: AsyncSession) -> None:
    """A model that has played games must stay resolvable, or its history becomes unreadable."""
    await sync_model_registry(db, [{"openrouter_id": "test/gone", "display_name": "Gone"}])
    await db.commit()

    report = await sync_model_registry(
        db, [{"openrouter_id": "test/here", "display_name": "Here"}], disable_missing=True
    )
    await db.commit()

    assert report.disabled == ["test/gone"]

    models = await playable_models(db)
    assert [m.openrouter_id for m in models] == ["test/here"]


@pytest.mark.integration
async def test_playable_models_exclude_tool_incapable(db: AsyncSession) -> None:
    await sync_model_registry(
        db,
        [
            {"openrouter_id": "a/tools", "display_name": "A", "supports_tools": True},
            {"openrouter_id": "b/no-tools", "display_name": "B", "supports_tools": False},
        ],
    )
    await db.commit()

    assert [m.openrouter_id for m in await playable_models(db)] == ["a/tools"]


@pytest.mark.integration
async def test_free_only_filter(db: AsyncSession) -> None:
    await sync_model_registry(db, load_seed())
    await db.commit()

    free = await playable_models(db, free_only=True)

    assert free, "the seed should carry free models for the filter to find"
    assert all(model.is_free for model in free)
    assert all(model.openrouter_id.endswith(":free") for model in free)


@pytest.mark.integration
async def test_pricing_table_is_built_from_the_database(db: AsyncSession) -> None:
    """At runtime the registry is the authority; the seed file only bootstraps it.

    Asserts a property, not a named model — see the note in `test_pricing.py`.
    """
    await sync_model_registry(db, load_seed())
    await db.commit()

    table = await load_pricing_table(db)

    assert len(table) >= 10

    free = [slug for slug in table.slugs() if slug.endswith(":free")]
    assert free, "the registry should carry at least one free model"
    for slug in free:
        pricing = table.get(slug)
        assert pricing is not None
        assert pricing.is_free

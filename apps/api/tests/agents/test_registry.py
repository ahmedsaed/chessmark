"""Model registry sync."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import (
    fetch_catalogue,
    fetch_endpoints,
    fits_a_game,
    is_batch,
    is_floating_alias,
    load_pricing_table,
    playable_models,
    provider_of,
    sync_model_registry,
    to_registry_entry,
)


def catalogue(*models: dict) -> list[dict]:
    """The entries `fetch_catalogue` would produce for these OpenRouter payloads.

    Hand-built rather than fetched: the suite never calls a provider, and a test that depended on
    OpenRouter's live catalogue would fail whenever a vendor withdrew a model.
    """
    return [to_registry_entry(model) for model in models]


def payload(
    model_id: str, *, prompt: str = "0.000001", completion: str = "0.000002", tools: bool = True
) -> dict:
    return {
        "id": model_id,
        "name": model_id,
        "context_length": 128_000,
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": ["tools"] if tools else [],
    }


SEED = catalogue(
    payload("vendor/cheap"),
    payload("vendor/free:free", prompt="0", completion="0"),
    payload("other/model", prompt="0.00001", completion="0.00003"),
)


def test_provider_is_the_vendor_half_of_the_slug() -> None:
    assert provider_of("nvidia/nemotron-nano-9b-v2:free") == "nvidia"
    assert provider_of("openai/gpt-oss-20b:free") == "openai"
    assert provider_of("bare-name") == "unknown"


async def test_the_catalogue_keeps_only_tool_capable_models() -> None:
    """AGENT-01: a model that cannot call tools cannot play, so none should be registered."""

    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "data": [
                    payload("vendor/with-tools"),
                    payload("vendor/without-tools", tools=False),
                ]
            }

    class FakeClient:
        @staticmethod
        async def get(url: str) -> FakeResponse:
            return FakeResponse()

    entries = await fetch_catalogue(FakeClient())  # type: ignore[arg-type]

    assert [entry["openrouter_id"] for entry in entries] == ["vendor/with-tools"]


def test_batch_variants_are_recognised() -> None:
    assert is_batch("openai/gpt-5.5-pro:batch")
    assert not is_batch("openai/gpt-5.5-pro")
    assert not is_batch("meta/llama:free")
    # `:thinking` is synchronous and perfectly playable — only `:batch` is asynchronous.
    assert not is_batch("qwen/qwen3:thinking")


async def test_batch_variants_are_never_registered() -> None:
    """They cannot play, and an unplayable model does not fail politely.

    A `:batch` model is served asynchronously, so a turn blocks until its 600-second ceiling and
    then **forfeits the model** — recording a loss against a model that never moved. Nothing in
    the data marks them: they declare `tools`, carry active endpoints, and report 99% uptime.
    """

    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "data": [
                    payload("vendor/model"),
                    payload("vendor/model:batch"),
                ]
            }

    class FakeClient:
        @staticmethod
        async def get(url: str) -> FakeResponse:
            return FakeResponse()

    entries = await fetch_catalogue(FakeClient())  # type: ignore[arg-type]

    assert [entry["openrouter_id"] for entry in entries] == ["vendor/model"]


def test_a_negative_price_is_read_as_unknown_not_as_a_discount() -> None:
    """OpenRouter reports -1 for the `openrouter/auto` routers, whose price it cannot state.

    Left signed, such a model looks like it *earns* money and sorts to the top of any
    cheapest-first ordering — which is exactly where a cost-conscious picker would land on it.
    """
    entry = to_registry_entry(payload("openrouter/auto", prompt="-1", completion="-1"))

    assert entry["prompt_usd_per_token"] == Decimal(0)
    assert entry["completion_usd_per_token"] == Decimal(0)


def test_prices_keep_their_full_precision() -> None:
    """Twelve decimal places survive the trip. A float would round exactly what invariant 4 measures."""
    entry = to_registry_entry(payload("vendor/precise", prompt="0.000000123456"))

    assert entry["prompt_usd_per_token"] == Decimal("0.000000123456")


@pytest.mark.integration
async def test_sync_creates_then_is_idempotent(db: AsyncSession) -> None:
    entries = SEED

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
    await sync_model_registry(db, SEED)
    await db.commit()

    free = await playable_models(db, free_only=True)

    assert free, "the seed should carry free models for the filter to find"
    assert all(model.is_free for model in free)
    assert all(model.openrouter_id.endswith(":free") for model in free)


@pytest.mark.integration
async def test_pricing_table_is_built_from_the_database(db: AsyncSession) -> None:
    """At runtime the registry is the authority — there is no file left to price against.

    Asserts a property, not a named model: vendors withdraw models, and a test pinned to one
    breaks for no useful reason.
    """
    await sync_model_registry(db, SEED)
    await db.commit()

    table = await load_pricing_table(db)

    assert len(table) == len(SEED)

    free = [slug for slug in table.slugs() if slug.endswith(":free")]
    assert free, "the registry should carry at least one free model"
    for slug in free:
        pricing = table.get(slug)
        assert pricing is not None
        assert pricing.is_free


# ====================================================================== context window (AGENT-14)


def test_a_window_too_small_for_a_game_is_rejected() -> None:
    """Measured, not assumed: the transcript grows ~1,818 tokens a ply, so a 32k window is spent
    around ply 20 of a possible 300 — and `context_exceeded` is a forfeit."""
    assert not fits_a_game(32_768, 128_000)
    assert fits_a_game(128_000, 128_000)
    assert fits_a_game(1_000_000, 128_000)


def test_an_undeclared_window_is_kept() -> None:
    """Unknown is not the same as small.

    Excluding on missing metadata would silently drop models over a gap in someone else's data,
    which is a worse failure than letting one through — the ply cap still bounds the game.
    """
    assert fits_a_game(None, 128_000)


def test_the_check_can_be_turned_off() -> None:
    assert fits_a_game(1_000, 0)


async def test_a_model_too_small_to_play_is_never_registered() -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            small = payload("vendor/tiny")
            small["context_length"] = 8_192
            return {"data": [payload("vendor/roomy"), small]}

    class FakeClient:
        @staticmethod
        async def get(url: str) -> FakeResponse:
            return FakeResponse()

    entries = await fetch_catalogue(FakeClient(), min_context=128_000)  # type: ignore[arg-type]

    assert [entry["openrouter_id"] for entry in entries] == ["vendor/roomy"]


# ====================================================================== floating aliases


def test_a_floating_alias_is_recognised() -> None:
    assert is_floating_alias("~anthropic/claude-opus-latest")
    assert is_floating_alias("google/gemini-flash-latest")
    assert not is_floating_alias("google/gemini-3.7-flash")
    # A version in the name is the opposite of floating — it says exactly what it is.
    assert not is_floating_alias("anthropic/claude-sonnet-4.5")


async def test_a_floating_alias_is_never_registered() -> None:
    """It can play perfectly well; what it cannot do is say what played.

    BENCH-04 requires a run to record its model version, and `-latest` cannot — so the record is
    unreproducible whether or not anyone rates it. ADR-0015 originally kept these playable and
    merely unrankable, which was half a decision.
    """

    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "data": [
                    payload("vendor/model-3.7"),
                    payload("vendor/model-latest"),
                    payload("~vendor/model-pinned"),
                ]
            }

    class FakeClient:
        @staticmethod
        async def get(url: str) -> FakeResponse:
            return FakeResponse()

    entries = await fetch_catalogue(FakeClient())  # type: ignore[arg-type]

    assert [entry["openrouter_id"] for entry in entries] == ["vendor/model-3.7"]


# ====================================================================== endpoints


class _RecordingClient:
    """Captures the URL asked for, and answers with one endpoint."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str) -> _RecordingResponse:
        self.urls.append(url)
        return _RecordingResponse()


class _RecordingResponse:
    @staticmethod
    def raise_for_status() -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {"data": {"endpoints": [{"provider_name": "Decart", "quantization": "fp4"}]}}


async def test_the_free_suffix_is_kept_in_the_endpoints_path() -> None:
    """The regression that made every free model unplayable.

    A `:free` variant is served by an entirely different — usually single — provider from the paid
    one. The suffix was stripped here on the belief that the endpoints route rejected it, so the
    paid variant's providers were stored against the free slug; the seat then pinned the
    highest-uptime one of those (ADR-0015), which does not serve the free model at all, and every
    free game died at ply 0 with a 404 naming a provider we had never selected.
    """
    client = _RecordingClient()

    await fetch_endpoints(client, "z-ai/glm-5.2:free")

    assert client.urls == ["https://openrouter.ai/api/v1/models/z-ai/glm-5.2:free/endpoints"]


async def test_a_paid_slug_is_unchanged() -> None:
    client = _RecordingClient()

    await fetch_endpoints(client, "google/gemini-3.7-flash")

    assert client.urls == ["https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints"]

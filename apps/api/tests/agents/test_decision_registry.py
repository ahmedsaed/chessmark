"""Decision models in the catalogue, the picker and the tournament field (ADR-0049)."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.decision_check import check_decision_models
from chessmark.agents.decision_request import DECISION_VERSION
from chessmark.agents.decisions import DecisionGateway
from chessmark.agents.registry import (
    DECISION_MODELS_URL,
    MODELS_URL,
    endpoint_is_playable,
    fetch_catalogue,
    ineligible_reasons,
    playable_models,
    select_endpoint,
    sync_model_registry,
)
from chessmark.agents.scripted_decisions import deciding
from chessmark.db import tournaments as repo
from chessmark.db.enums import ModelRuntime
from chessmark.db.models import ModelEndpoint, ModelRegistry, Tournament
from chessmark.tournament import FieldFilter

#: The two listings as OpenRouter serves them: the main one has no decision model in it at all.
CHAT_ENTRY = {
    "id": "vendor/chat",
    "name": "Chat",
    "context_length": 200_000,
    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    "supported_parameters": ["tools", "reasoning"],
    "architecture": {"output_modalities": ["text"]},
}
DECISION_ENTRY = {
    "id": "typesafe/jev-1.13",
    "name": "TypeSafe: Jev 1.13",
    "context_length": 32_000,
    "pricing": {"prompt": "0.000000042", "completion": "0"},
    "supported_parameters": [],
    "architecture": {"output_modalities": ["decisions"]},
}
ALIAS_ENTRY = {**DECISION_ENTRY, "id": "~typesafe/jev-latest"}


class _Response:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": self._data}


class _Client:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str) -> _Response:
        self.urls.append(url)
        if url == DECISION_MODELS_URL:
            return _Response([DECISION_ENTRY, ALIAS_ENTRY])
        return _Response([CHAT_ENTRY])


async def test_the_catalogue_asks_for_decision_models_separately_and_keeps_them() -> None:
    client = _Client()
    entries = await fetch_catalogue(client, min_context=64_000)  # type: ignore[arg-type]

    assert client.urls == [MODELS_URL, DECISION_MODELS_URL]
    by_slug = {e["openrouter_id"]: e for e in entries}
    # No tools and a 32k window: both would refuse a chat model; neither applies to this one.
    assert by_slug["typesafe/jev-1.13"]["runtime"] is ModelRuntime.DECISION
    assert by_slug["vendor/chat"]["runtime"] is ModelRuntime.LLM
    # The alias is still refused: it cannot say which build played.
    assert "~typesafe/jev-latest" not in by_slug


async def _seed(db: AsyncSession) -> None:
    client = _Client()
    entries = await fetch_catalogue(client, min_context=64_000)  # type: ignore[arg-type]
    await sync_model_registry(db, entries)
    # The catalogue refresh's own step, against a scripted host that answers (ADR-0051).
    await check_decision_models(db, DecisionGateway(decide_fn=deciding()))
    rows = {r.openrouter_id: r for r in await db.scalars(sa.select(ModelRegistry))}
    db.add(
        ModelEndpoint(
            model_id=rows["typesafe/jev-1.13"].id,
            provider_name="TypeSafe",
            context_length=32_000,
            supports_tools=False,
            uptime_1d=100.0,
        )
    )
    db.add(
        ModelEndpoint(
            model_id=rows["vendor/chat"].id,
            provider_name="Chatty",
            context_length=200_000,
            supports_tools=True,
            uptime_1d=100.0,
        )
    )
    # A chat model with an endpoint that cannot call tools — the rule the decision exemption must
    # not weaken.
    blind = ModelRegistry(
        openrouter_id="vendor/blind", display_name="Blind", provider="vendor", supports_tools=True
    )
    db.add(blind)
    await db.flush()
    db.add(ModelEndpoint(model_id=blind.id, provider_name="Nope", supports_tools=False))
    await db.flush()


async def test_a_decision_model_is_playable_and_a_chat_model_without_tools_is_not(
    db: AsyncSession,
) -> None:
    await _seed(db)
    playable = {m.openrouter_id for m in await playable_models(db, min_context=64_000)}
    assert "typesafe/jev-1.13" in playable

    with_endpoint = set(
        await db.scalars(
            sa.select(ModelRegistry.openrouter_id).where(
                ModelRegistry.id.in_(
                    sa.select(ModelEndpoint.model_id).where(*endpoint_is_playable(64_000))
                )
            )
        )
    )
    assert with_endpoint == {"typesafe/jev-1.13", "vendor/chat"}


async def test_a_decision_seat_pins_its_endpoint(db: AsyncSession) -> None:
    await _seed(db)
    endpoint = await select_endpoint(db, model_slug="typesafe/jev-1.13")
    assert endpoint.provider_name == "TypeSafe"


async def test_the_chat_rules_are_not_reasons_against_a_decision_model(db: AsyncSession) -> None:
    await _seed(db)
    row = (
        await db.scalars(
            sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == "typesafe/jev-1.13")
        )
    ).one()
    assert ineligible_reasons(row, min_context=64_000) == []


async def test_a_field_is_one_kind_of_model(db: AsyncSession) -> None:
    await _seed(db)
    chat = await repo.resolve_field(db, FieldFilter())
    decision = await repo.resolve_field(db, FieldFilter(runtime="decision"))
    assert [e.key for e in chat] == ["vendor/chat"]
    assert [e.key for e in decision] == ["typesafe/jev-1.13"]


def test_a_field_names_a_runtime_that_exists() -> None:
    with pytest.raises(ValueError, match="runtime"):
        FieldFilter(runtime="both")
    assert "decision models" in FieldFilter(runtime="decision").describe()


def test_a_stored_field_from_before_runtimes_is_a_chat_field() -> None:
    assert repo.filter_from_json({"free_only": True}).runtime == "llm"
    assert repo.filter_from_json({"runtime": "decision"}).runtime == "decision"


def test_each_kind_of_event_plays_its_own_era() -> None:
    chat = Tournament(name="c", slug="c", field_filter={})
    decision = Tournament(name="d", slug="d", field_filter={"runtime": "decision"})
    assert repo.era_of(chat) == repo.current_era()
    assert repo.era_of(decision) == DECISION_VERSION.split(".")[0]
    assert repo.era_of(chat) != repo.era_of(decision)

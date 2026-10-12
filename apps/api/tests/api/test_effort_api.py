"""Reasoning levels through the API (ADR-0067).

The catalogue says which levels a model offers and which it plays at by default; starting a game
can name one, and a level the model does not list is a sentence, not a game seated at a different
contestant. The game then says what each seat played at.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_endpoints, sync_model_registry, to_registry_entry
from chessmark.db.models import ModelRegistry
from tests.api.conftest import as_user, fund

pytestmark = pytest.mark.integration

ALWAYS_ON = {
    "mandatory": True,
    "default_enabled": True,
    "supported_efforts": ["high", "medium", "low"],
    "default_effort": "medium",
}


async def _model(
    db: AsyncSession, slug: str, endpoints: list[dict[str, Any]] | None = None
) -> None:
    entry = to_registry_entry(
        {
            "id": slug,
            "name": slug,
            "context_length": 128_000,
            "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
            "supported_parameters": ["tools", "reasoning"],
            "reasoning": ALWAYS_ON,
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
    await db.commit()


async def _listed(client: AsyncClient, slug: str) -> dict[str, Any]:
    return next(m for m in (await client.get("/models")).json() if m["openrouter_id"] == slug)


async def test_the_catalogue_says_which_levels_a_model_offers(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(db, "test/levels")

    body = await _listed(client, "test/levels")

    assert body["reasoning_levels"] == ["low", "medium", "high"]
    assert body["default_reasoning"] == "medium"


async def test_a_host_that_ignores_reasoning_is_not_offered_for_a_model_that_reasons(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Every seat on this model sends a level, so that host could never be pinned — and offering
    it would be the form promising what `POST /games` refuses."""
    await _model(
        db,
        "test/deaf-host",
        [
            {
                "provider_name": "Listens",
                "quantization": "fp8",
                "supported_parameters": ["tools", "reasoning"],
                "uptime_last_1d": 90.0,
            },
            {
                "provider_name": "Deaf",
                "quantization": "fp8",
                "supported_parameters": ["tools"],
                "uptime_last_1d": 99.9,
            },
        ],
    )

    body = await _listed(client, "test/deaf-host")

    hosts = [e["provider"] for c in body["contestants"] for e in c["endpoints"]]
    assert hosts == ["Listens"]


async def test_a_game_can_be_started_at_a_level_and_says_so(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    await _model(db, "test/chosen-level")
    await fund(db, "user_levels")

    response = await client.post(
        "/games",
        json={
            "white": "test/chosen-level",
            "black": "test/chosen-level",
            "white_effort": "high",
            "max_plies": 4,
        },
        headers=as_user("user_levels"),
    )

    assert response.status_code == 201, response.text
    detail = (await client.get(f"/games/{response.json()['id']}")).json()
    seats = {p["colour"]: p for p in detail["players"]}
    assert seats["white"]["effort"] == "high"
    assert seats["black"]["effort"] == "medium", "the default, recorded rather than left implicit"
    assert seats["white"]["effort_inferred"] is False


async def test_a_level_against_a_person(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    await _model(db, "test/human-level")
    await fund(db, "user_human_level")

    response = await client.post(
        "/games/human",
        json={"model": "test/human-level", "model_effort": "low", "max_plies": 4},
        headers=as_user("user_human_level"),
    )

    assert response.status_code == 201, response.text
    detail = (await client.get(f"/games/{response.json()['id']}")).json()
    machine = next(p for p in detail["players"] if p["kind"] == "model")
    assert machine["effort"] == "low"


async def test_a_level_the_model_does_not_offer_is_a_400(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    """Not the nearest level it does offer: that would seat a different contestant without saying."""
    await _model(db, "test/no-max")
    await fund(db, "user_no_max")

    response = await client.post(
        "/games",
        json={
            "white": "test/no-max",
            "black": "test/no-max",
            "white_effort": "max",
            "max_plies": 4,
        },
        headers=as_user("user_no_max"),
    )

    assert response.status_code == 400, response.text
    assert "max" in response.text

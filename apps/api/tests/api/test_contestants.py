"""What the API says about contestants and endpoints (ADR-0015).

The old vocabulary described a filter — `playable_quantizations`, "blocked on precision" — for a
policy that no longer exists. A contestant is `(model, quantization)`, and the useful thing to say
about one is which endpoint would actually serve it.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.db.models import ModelEndpoint, ModelRegistry
from tests.api.conftest import as_user, fund
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration


async def _model(db: AsyncSession, slug: str, endpoints: list[dict]) -> None:
    await sync_model_registry(db, [{"openrouter_id": slug, "display_name": slug}])
    await db.flush()
    model_id = await db.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slug)
    )
    for endpoint in endpoints:
        db.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name=endpoint["provider"],
                quantization=endpoint.get("quantization"),
                uptime_1d=endpoint.get("uptime"),
                supports_implicit_caching=endpoint.get("caching"),
                supports_tools=endpoint.get("supports_tools", True),
                context_length=endpoint.get("context"),
                throughput=endpoint.get("throughput"),
            )
        )
    await db.commit()


def contestant(body: dict, quantization: str) -> dict:
    return next(c for c in body["contestants"] if c["quantization"] == quantization)


# ====================================================================== the model card


async def test_each_precision_is_its_own_contestant(client: AsyncClient, db: AsyncSession) -> None:
    """`model@fp4` and `model@fp8` are different entrants, so the card lists both rather than
    marking one 'blocked'."""
    await _model(
        db,
        "test/twoways",
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Four", "quantization": "fp4", "uptime": 95.0},
        ],
    )

    body = next(
        m for m in (await client.get("/models")).json() if m["openrouter_id"] == "test/twoways"
    )

    assert {c["quantization"] for c in body["contestants"]} == {"fp8", "fp4"}


async def test_a_contestant_names_the_endpoint_that_would_serve_it(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The card has to agree with what a match would actually pin, or it is telling a story."""
    await _model(
        db,
        "test/named",
        [
            {"provider": "Healthy", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Flaky", "quantization": "fp8", "uptime": 80.0},
        ],
    )

    body = next(
        m for m in (await client.get("/models")).json() if m["openrouter_id"] == "test/named"
    )

    assert contestant(body, "fp8")["provider"] == "Healthy"
    assert contestant(body, "fp8")["endpoint_count"] == 2


async def test_a_contestant_lists_every_endpoint_healthiest_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The game form offers these as the choice of host (ADR-0066), and its default has to be
    the one the rule would pin — so the order is the pinning order, not the database's."""
    await _model(
        db,
        "test/hosts",
        [
            {"provider": "Middling", "quantization": "fp8", "uptime": 95.0, "throughput": 40.0},
            {"provider": "Healthy", "quantization": "fp8", "uptime": 99.9, "throughput": 10.0},
            {"provider": "Flaky", "quantization": "fp8", "uptime": 80.0, "throughput": 90.0},
            {"provider": "Quarter", "quantization": "fp4", "uptime": 99.0},
        ],
    )

    body = next(
        m for m in (await client.get("/models")).json() if m["openrouter_id"] == "test/hosts"
    )

    fp8 = contestant(body, "fp8")
    assert [e["provider"] for e in fp8["endpoints"]] == ["Healthy", "Middling", "Flaky"]
    assert fp8["endpoints"][0]["provider"] == fp8["provider"]
    assert fp8["endpoints"][1]["throughput"] == 40.0
    # Each precision lists only its own hosts: choosing Quarter would seat a different entrant.
    assert [e["provider"] for e in contestant(body, "fp4")["endpoints"]] == ["Quarter"]


async def test_an_endpoint_too_small_for_a_game_is_not_offered(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Listed by `is_active` alone, an endpoint under the context floor would be offered by the
    form and refused by `POST /games` — the catalogue and the pin have to share one predicate."""
    await _model(
        db,
        "test/cramped",
        [
            {"provider": "Roomy", "quantization": "fp8", "uptime": 90.0},
            {"provider": "Cramped", "quantization": "fp8", "uptime": 99.9, "context": 1024},
        ],
    )

    body = next(
        m for m in (await client.get("/models")).json() if m["openrouter_id"] == "test/cramped"
    )

    assert [e["provider"] for e in contestant(body, "fp8")["endpoints"]] == ["Roomy"]
    assert contestant(body, "fp8")["provider"] == "Roomy"


async def test_caching_support_is_not_published() -> None:
    """OpenRouter's `supports_implicit_caching` is stored but never served.

    It does not predict behaviour: `false` for endpoints we measured at 91-94% hit rate
    (Azure/gpt-5.4-mini, Baidu/deepseek-v4-flash, StreamLake/kimi-k2.5), `true` for the one
    measured at 28% (Google/gemini-3.7-flash). An API field that is wrong more often than right is
    worse than an absent one.
    """
    from chessmark.api.schemas import ContestantOut

    assert "supports_implicit_caching" not in ContestantOut.model_fields


async def test_undeclared_precision_is_a_contestant_not_an_exclusion(
    client: AsyncClient, db: AsyncSession
) -> None:
    """ADR-0014 refused `unknown`; ADR-0015 records it. A closed-weight model has nothing to
    disclose, and excluding it selects for open weights rather than measurement quality."""
    await _model(db, "test/closed", [{"provider": "Vendor", "quantization": None, "uptime": 99.0}])

    body = next(
        m for m in (await client.get("/models")).json() if m["openrouter_id"] == "test/closed"
    )

    assert contestant(body, "unknown")["provider"] == "Vendor"


async def test_an_endpoint_without_tools_is_not_a_contestant(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _model(
        db,
        "test/notools",
        [{"provider": "Chat", "quantization": "fp8", "uptime": 99.0, "supports_tools": False}],
    )

    # Absent from the default listing entirely — it has no playable endpoint, so it is not an
    # offer. The registry view still holds it, with no contestants.
    offered = {m["openrouter_id"] for m in (await client.get("/models")).json()}
    assert "test/notools" not in offered

    registry = (await client.get("/models", params={"playable": False})).json()
    body = next(m for m in registry if m["openrouter_id"] == "test/notools")

    assert body["contestants"] == []


# ====================================================================== the seat


async def test_a_seat_reports_the_endpoint_it_was_pinned_to(
    client: AsyncClient, game: Fixture
) -> None:
    """`pinned_provider` is what a reader needs to know a result is reproducible."""
    body = (await client.get(f"/games/{game.game.id}")).json()

    assert all("pinned_provider" in player for player in body["players"])


# ====================================================================== choosing one


async def test_a_precision_can_be_requested_when_starting_a_game(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    await _model(
        db,
        "test/pickable",
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Four", "quantization": "fp4", "uptime": 90.0},
        ],
    )

    await fund(db, "user_picky")
    response = await client.post(
        "/games",
        json={
            "white": "test/pickable",
            "black": "test/pickable",
            "white_quantization": "fp4",
            "black_quantization": "fp8",
            "max_plies": 4,
        },
        headers=as_user("user_picky"),
    )

    assert response.status_code == 201, response.text
    detail = (await client.get(f"/games/{response.json()['id']}")).json()
    seats = {p["colour"]: p for p in detail["players"]}
    assert seats["white"]["pinned_provider"] == "Four"
    assert seats["black"]["pinned_provider"] == "Eight"


async def test_asking_for_a_precision_nobody_serves_is_a_400(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    """Not a 500, and not a silent substitution — seating a different precision would measure a
    different contestant with no way for the caller to know."""
    await _model(
        db, "test/eightonly", [{"provider": "Eight", "quantization": "fp8", "uptime": 99.0}]
    )
    await fund(db, "user_wrong_precision")

    response = await client.post(
        "/games",
        json={
            "white": "test/eightonly",
            "black": "test/eightonly",
            "white_quantization": "fp4",
            "max_plies": 4,
        },
        headers=as_user("user_wrong_precision"),
    )

    assert response.status_code == 400
    assert "fp4" in response.text


async def test_an_endpoint_can_be_chosen_when_starting_a_game(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    """The point of ADR-0066: the healthiest host by uptime is not always the one that plays
    well, and the person paying for the game can say which they want."""
    await _model(
        db,
        "test/choosy",
        [
            {"provider": "Healthy", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Preferred", "quantization": "fp8", "uptime": 90.0},
        ],
    )
    await fund(db, "user_choosy")

    response = await client.post(
        "/games",
        json={
            "white": "test/choosy",
            "black": "test/choosy",
            "white_provider": "Preferred",
            "max_plies": 4,
        },
        headers=as_user("user_choosy"),
    )

    assert response.status_code == 201, response.text
    detail = (await client.get(f"/games/{response.json()['id']}")).json()
    seats = {p["colour"]: p for p in detail["players"]}
    assert seats["white"]["pinned_provider"] == "Preferred"
    assert seats["black"]["pinned_provider"] == "Healthy"


async def test_an_endpoint_can_be_chosen_against_a_person(
    client: AsyncClient, db: AsyncSession, redis: object
) -> None:
    await _model(
        db,
        "test/choosy-human",
        [
            {"provider": "Healthy", "quantization": "fp8", "uptime": 99.9},
            {"provider": "Preferred", "quantization": "fp8", "uptime": 90.0},
        ],
    )
    await fund(db, "user_choosy_human")

    response = await client.post(
        "/games/human",
        json={"model": "test/choosy-human", "model_provider": "Preferred", "max_plies": 4},
        headers=as_user("user_choosy_human"),
    )

    assert response.status_code == 201, response.text
    detail = (await client.get(f"/games/{response.json()['id']}")).json()
    machine = next(p for p in detail["players"] if p["kind"] == "model")
    assert machine["pinned_provider"] == "Preferred"


@pytest.mark.parametrize(
    ("provider", "quantization"),
    [
        ("Nobody", None),  # not a host of this model at all
        ("Toolless", None),  # a host, but one that cannot play
        ("Four", "fp8"),  # a host, at a different precision than asked for
    ],
)
async def test_asking_for_an_endpoint_that_cannot_serve_the_seat_is_a_400(
    client: AsyncClient,
    db: AsyncSession,
    redis: object,
    provider: str,
    quantization: str | None,
) -> None:
    """Before ADR-0066 a named provider went into `only` unchecked. From a form, a stale or wrong
    name would start a game that died at ply 0 on OpenRouter's 404 — a sentence now, not a corpse."""
    await _model(
        db,
        "test/strict",
        [
            {"provider": "Eight", "quantization": "fp8", "uptime": 99.0},
            {"provider": "Four", "quantization": "fp4", "uptime": 99.0},
            {
                "provider": "Toolless",
                "quantization": "fp8",
                "uptime": 99.9,
                "supports_tools": False,
            },
        ],
    )
    await fund(db, "user_strict")

    response = await client.post(
        "/games",
        json={
            "white": "test/strict",
            "black": "test/strict",
            "white_provider": provider,
            "white_quantization": quantization,
            "max_plies": 4,
        },
        headers=as_user("user_strict"),
    )

    assert response.status_code == 400, response.text
    assert provider in response.text

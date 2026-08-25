"""The Phase 9 hard gate, at the HTTP boundary.

Two rules, and the whole phase is about not confusing them: **reading is open to everyone, writing
costs money and is not.** A regression in either direction is serious — a 401 on the lobby breaks
the public product, and a 200 on game creation means anyone can spend our budget.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.db.models import Game, User
from tests.api.conftest import as_user, fund
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration


async def _playable(db: AsyncSession) -> tuple[str, str]:
    """Two tool-capable models in the registry, so creation fails on auth rather than on input."""
    await sync_model_registry(
        db,
        [
            {
                "openrouter_id": slug,
                "display_name": slug,
                "provider": "test",
                "context_length": 128_000,
                "prompt_usd_per_token": 0.0,
                "completion_usd_per_token": 0.0,
                "supports_reasoning": False,
                "supports_tools": True,
                "is_free": True,
                "enabled": True,
            }
            for slug in ("test/white", "test/black")
        ],
    )
    await db.commit()
    return "test/white", "test/black"


def _create_body(white: str, black: str) -> dict[str, Any]:
    return {"white": white, "black": black, "max_plies": 4}


# ====================================================================== reading stays open


async def test_the_lobby_needs_no_account(client: AsyncClient, game: Fixture) -> None:
    """AUTH-02. Spectating is the shareable surface; an account wall in front of it would defeat
    the entire point of the project being public."""
    assert (await client.get("/games")).status_code == 200


@pytest.mark.parametrize(
    "path",
    ["", "/plies", "/turns", "/messages", "/events", "/pgn"],
)
async def test_every_game_read_path_is_public(
    client: AsyncClient, game: Fixture, path: str
) -> None:
    response = await client.get(f"/games/{game.game.id}{path}")

    assert response.status_code == 200, f"/games/{{id}}{path} requires auth and should not"


async def test_the_model_list_is_public(client: AsyncClient) -> None:
    assert (await client.get("/models")).status_code == 200


async def test_health_is_public(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


# ====================================================================== writing is gated


async def test_creating_a_game_without_a_token_is_401(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The criterion. This endpoint spends real money."""
    white, black = await _playable(db)

    response = await client.post("/games", json=_create_body(white, black))

    assert response.status_code == 401


async def test_a_401_says_how_to_authenticate(client: AsyncClient, db: AsyncSession) -> None:
    """RFC 6750. It is also what tells a client this is an auth problem, not a permissions one."""
    white, black = await _playable(db)

    response = await client.post("/games", json=_create_body(white, black))

    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        {"authorization": "Bearer forged.token.here"},
        {"authorization": "Bearer "},
        {"authorization": "Basic dXNlcjpwYXNz"},
        {"authorization": "token-without-a-scheme"},
    ],
)
async def test_a_bad_token_cannot_create_a_game(
    client: AsyncClient, db: AsyncSession, header: dict[str, str]
) -> None:
    white, black = await _playable(db)

    response = await client.post("/games", json=_create_body(white, black), headers=header)

    assert response.status_code == 401


async def test_a_signed_in_user_can_create_a_game(
    client: AsyncClient, db: AsyncSession, redis: Any
) -> None:
    white, black = await _playable(db)
    await fund(db, "user_creator")

    response = await client.post(
        "/games", json=_create_body(white, black), headers=as_user("user_creator")
    )

    assert response.status_code == 201, response.text


async def test_creating_a_game_provisions_the_user_on_first_sight(
    client: AsyncClient, db: AsyncSession, redis: Any
) -> None:
    """Just-in-time provisioning. Waiting for the webhook would put Clerk's delivery latency in
    front of a new user's very first action."""
    white, black = await _playable(db)
    await fund(db, "user_brand_new")

    await client.post("/games", json=_create_body(white, black), headers=as_user("user_brand_new"))

    db.expunge_all()
    user = await db.scalar(sa.select(User).where(User.clerk_user_id == "user_brand_new"))
    assert user is not None


async def test_the_game_records_who_started_it(
    client: AsyncClient, db: AsyncSession, redis: Any
) -> None:
    """Without this the per-user ledger has nothing to attribute spend to."""
    white, black = await _playable(db)
    await fund(db, "user_owner")

    body = (
        await client.post("/games", json=_create_body(white, black), headers=as_user("user_owner"))
    ).json()

    db.expunge_all()
    stored = await db.get(Game, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.created_by_user_id is not None


# ====================================================================== the spend controls


async def test_a_user_out_of_credits_is_refused_with_a_reason(
    client: AsyncClient, db: AsyncSession, redis: Any
) -> None:
    """ADR-0016. Spent through the API, so the test exercises the real charging path."""
    white, black = await _playable(db)
    header = as_user("user_at_quota")

    # Both models are tier 1 here, so each game costs two credits — one per seat.
    await fund(db, "user_at_quota", credits=4)

    for _ in range(2):
        assert (
            await client.post("/games", json=_create_body(white, black), headers=header)
        ).status_code == 201

    refused = await client.post("/games", json=_create_body(white, black), headers=header)

    assert refused.status_code == 402
    assert "credit" in refused.text.lower()
    # The refusal has to say both numbers and how a balance changes, or it is a dead end.
    assert "you have 0" in refused.text.lower()
    assert "administrator" in refused.text.lower()


async def test_a_new_account_cannot_start_a_game(
    client: AsyncClient, db: AsyncSession, redis: Any
) -> None:
    """Zero by default is the point of the change: nobody plays until someone grants credits."""
    white, black = await _playable(db)

    refused = await client.post(
        "/games", json=_create_body(white, black), headers=as_user("user_unfunded")
    )

    assert refused.status_code == 402


async def test_the_global_kill_switch_refuses_new_games(
    client: AsyncClient, db: AsyncSession, redis: Any, monkeypatch: Any
) -> None:
    """AUTH-05 at the front door. The worker enforces it again before each call — layers, not one
    checkpoint (ADR-0011)."""
    from chessmark.core.budget import GlobalBudget
    from chessmark.core.config import get_settings

    white, black = await _playable(db)
    settings = get_settings()
    monkeypatch.setattr(settings, "global_daily_usd_budget", 1.0)

    await GlobalBudget(redis, daily_limit_usd=Decimal("1.00")).record(Decimal("1.00"))

    response = await client.post(
        "/games", json=_create_body(white, black), headers=as_user("user_after_switch")
    )

    assert response.status_code == 503
    assert "watching and replays are unaffected" in response.text


async def test_the_kill_switch_does_not_stop_spectating(
    client: AsyncClient, game: Fixture, redis: Any, monkeypatch: Any
) -> None:
    """The switch protects the budget, not the public site. Reading costs nothing."""
    from chessmark.core.budget import GlobalBudget
    from chessmark.core.config import get_settings

    monkeypatch.setattr(get_settings(), "global_daily_usd_budget", 1.0)
    await GlobalBudget(redis, daily_limit_usd=Decimal("1.00")).record(Decimal("5.00"))

    assert (await client.get(f"/games/{game.game.id}")).status_code == 200


async def test_the_per_game_cap_is_clamped_to_the_server_ceiling(
    client: AsyncClient, db: AsyncSession, redis: Any, monkeypatch: Any
) -> None:
    """Layer 3 must not be settable by the caller. A request asking for a $500 cap is clamped, not
    honoured and not refused."""
    from chessmark.core.config import get_settings

    white, black = await _playable(db)
    await fund(db, "user_greedy")
    monkeypatch.setattr(get_settings(), "max_usd_per_game", 0.25)

    body = (
        await client.post(
            "/games",
            json={**_create_body(white, black), "max_usd": "500.00"},
            headers=as_user("user_greedy"),
        )
    ).json()

    db.expunge_all()
    stored = await db.get(Game, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.max_usd == Decimal("0.25")


async def test_rapid_requests_are_rate_limited(
    client: AsyncClient, db: AsyncSession, redis: Any, monkeypatch: Any
) -> None:
    """AUTH-06. A balance alone would let someone spend every credit they hold in one second."""
    from chessmark.core.config import get_settings

    white, black = await _playable(db)
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_per_window", 3)
    header = as_user("user_fast")
    await fund(db, "user_fast")

    codes = [
        (await client.post("/games", json=_create_body(white, black), headers=header)).status_code
        for _ in range(6)
    ]

    assert codes.count(201) == 3, codes
    assert codes.count(429) == 3, codes


async def test_a_rate_limited_response_says_when_to_retry(
    client: AsyncClient, db: AsyncSession, redis: Any, monkeypatch: Any
) -> None:
    from chessmark.core.config import get_settings

    white, black = await _playable(db)
    await fund(db, "user_impatient")
    monkeypatch.setattr(get_settings(), "rate_limit_per_window", 1)
    header = as_user("user_impatient")

    await client.post("/games", json=_create_body(white, black), headers=header)
    refused = await client.post("/games", json=_create_body(white, black), headers=header)

    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1


# ====================================================================== /me


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/me")).status_code == 401


async def test_me_reports_the_credit_balance(client: AsyncClient, db: AsyncSession) -> None:
    """So the UI can say what you hold rather than letting you find out by being refused."""
    body = (await client.get("/me", headers=as_user("user_curious"))).json()

    # A new account holds nothing (ADR-0016).
    assert body["credit_balance"] == 0
    assert body["games_started_today"] == 0
    assert body["is_admin"] is False


async def test_me_reflects_a_grant(client: AsyncClient, db: AsyncSession) -> None:
    await fund(db, "user_granted", credits=7)

    body = (await client.get("/me", headers=as_user("user_granted"))).json()

    assert body["credit_balance"] == 7


async def test_a_read_only_request_still_provisions_the_user(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Provisioning must not depend on the endpoint happening to commit.

    The bug this pins: `/me` returned 200 with a correct quota readout while `users` stayed empty,
    because the upsert was rolled back when the session closed. It looked fine from the browser and
    persisted nothing. Game creation masked it — that endpoint commits, so it flushed the pending
    insert as a side effect.
    """
    response = await client.get("/me", headers=as_user("user_read_only"))
    assert response.status_code == 200

    # A different session, so this reads committed state rather than the request's own transaction.
    db.expunge_all()
    stored = await db.scalar(sa.select(User).where(User.clerk_user_id == "user_read_only"))
    assert stored is not None, "a signed-in read did not persist the user"


async def test_a_returning_user_is_not_duplicated(client: AsyncClient, db: AsyncSession) -> None:
    """The upsert runs on every authenticated request, so it must be idempotent."""
    header = as_user("user_returning")
    for _ in range(3):
        assert (await client.get("/me", headers=header)).status_code == 200

    db.expunge_all()
    rows = (await db.scalars(sa.select(User).where(User.clerk_user_id == "user_returning"))).all()
    assert len(rows) == 1

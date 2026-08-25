"""The admin surface and the Clerk webhook.

Both are places where getting authorisation wrong is quiet and expensive: an admin route open to
any signed-in user, or a webhook that accepts unsigned deliveries, would each hand over the user
table without anything looking broken.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.webhooks import sign
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, User
from chessmark.db.quotas import note_game_started
from tests.api.conftest import as_user
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration

SECRET = "whsec_" + base64.b64encode(b"an-admin-test-signing-secret-32!").decode()


async def _make_admin(db: AsyncSession, clerk_user_id: str) -> User:
    """Admin is set in the database by hand. There is no endpoint that grants it, on purpose."""
    user = User(clerk_user_id=clerk_user_id, email=f"{clerk_user_id}@test", is_admin=True)
    db.add(user)
    await db.commit()
    return user


# ====================================================================== admin authorisation


async def test_admin_routes_reject_anonymous_callers(client: AsyncClient) -> None:
    assert (await client.get("/admin/spend")).status_code == 401


async def test_admin_routes_reject_ordinary_users(client: AsyncClient, db: AsyncSession) -> None:
    """The important one. A signed-in user is authenticated but not authorised, and conflating the
    two is how admin surfaces leak."""
    response = await client.get("/admin/spend", headers=as_user("user_ordinary"))

    assert response.status_code == 403


async def test_an_admin_can_read_spend(client: AsyncClient, db: AsyncSession) -> None:
    await _make_admin(db, "user_admin")

    response = await client.get("/admin/spend", headers=as_user("user_admin"))

    assert response.status_code == 200
    body = response.json()
    assert "spent_today_usd" in body
    assert body["tripped"] is False


async def test_spend_reports_both_the_counter_and_the_recorded_total(
    client: AsyncClient, db: AsyncSession, redis: Any
) -> None:
    """Reported side by side because a gap between them means one is wrong, and an operator should
    be able to see that rather than trust a single number."""
    from chessmark.core.budget import GlobalBudget

    await _make_admin(db, "user_admin")
    await GlobalBudget(redis, daily_limit_usd=Decimal("10")).record(Decimal("0.50"))

    body = (await client.get("/admin/spend", headers=as_user("user_admin"))).json()

    assert Decimal(body["spent_today_usd"]) == Decimal("0.50")
    assert "lifetime_recorded_usd" in body


# ====================================================================== quota reset


async def test_an_admin_can_reset_a_users_quota(client: AsyncClient, db: AsyncSession) -> None:
    await _make_admin(db, "user_admin")
    victim = User(clerk_user_id="user_victim")
    db.add(victim)
    await db.flush()
    await note_game_started(db, victim.id)
    await db.commit()

    response = await client.post(
        f"/admin/users/{victim.id}/usage/reset", headers=as_user("user_admin")
    )

    assert response.status_code == 200
    assert response.json()["games_started"] == 0


async def test_an_ordinary_user_cannot_reset_a_quota(client: AsyncClient, db: AsyncSession) -> None:
    victim = User(clerk_user_id="user_victim2")
    db.add(victim)
    await db.commit()

    response = await client.post(
        f"/admin/users/{victim.id}/usage/reset", headers=as_user("user_nobody")
    )

    assert response.status_code == 403


async def test_resetting_an_unknown_user_is_a_404(client: AsyncClient, db: AsyncSession) -> None:
    await _make_admin(db, "user_admin")

    response = await client.post(
        f"/admin/users/{uuid.uuid4()}/usage/reset", headers=as_user("user_admin")
    )

    assert response.status_code == 404


# ====================================================================== cancelling a game


async def test_an_admin_can_cancel_a_running_game(
    client: AsyncClient, db: AsyncSession, game: Fixture
) -> None:
    await _make_admin(db, "user_admin")

    response = await client.post(
        f"/admin/games/{game.game.id}/cancel", headers=as_user("user_admin")
    )

    assert response.status_code == 204
    db.expunge_all()
    stored = await db.get(Game, game.game.id)
    assert stored is not None
    assert stored.status is GameStatus.ABORTED


async def test_a_cancelled_game_is_not_a_loss_for_anyone(
    client: AsyncClient, db: AsyncSession, game: Fixture
) -> None:
    """An operator's intervention is not a chess result. Letting one into the record would corrupt
    exactly the number this project publishes."""
    await _make_admin(db, "user_admin")

    await client.post(f"/admin/games/{game.game.id}/cancel", headers=as_user("user_admin"))

    db.expunge_all()
    stored = await db.get(Game, game.game.id)
    assert stored is not None
    assert stored.result.value == "*"
    assert stored.winner_colour is None


async def test_cancelling_a_finished_game_is_a_conflict(
    client: AsyncClient, db: AsyncSession, game: Fixture
) -> None:
    await _make_admin(db, "user_admin")
    await client.post(f"/admin/games/{game.game.id}/cancel", headers=as_user("user_admin"))

    again = await client.post(f"/admin/games/{game.game.id}/cancel", headers=as_user("user_admin"))

    assert again.status_code == 409


async def test_an_ordinary_user_cannot_cancel_a_game(
    client: AsyncClient, db: AsyncSession, game: Fixture
) -> None:
    response = await client.post(
        f"/admin/games/{game.game.id}/cancel", headers=as_user("user_meddler")
    )

    assert response.status_code == 403
    db.expunge_all()
    stored = await db.get(Game, game.game.id)
    assert stored is not None
    assert stored.status is GameStatus.RUNNING


# ====================================================================== the webhook


def _signed(body: dict[str, Any], *, secret: str = SECRET) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    message_id, timestamp = "msg_test", str(int(time.time()))
    return raw, {
        "svix-id": message_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{sign(secret, message_id=message_id, timestamp=timestamp, body=raw)}",
    }


@pytest.fixture
def webhook_secret(monkeypatch: Any) -> str:
    from chessmark.core.config import get_settings

    monkeypatch.setattr(get_settings(), "clerk_webhook_secret", SECRET)
    return SECRET


async def test_an_unsigned_delivery_is_refused(
    client: AsyncClient, db: AsyncSession, webhook_secret: str
) -> None:
    """This endpoint writes to `users` and has no other authentication. If the signature check is
    wrong, anyone who finds the URL can delete accounts."""
    body = {"type": "user.created", "data": {"id": "user_forged"}}

    response = await client.post("/webhooks/clerk", json=body)

    assert response.status_code == 401
    db.expunge_all()
    assert await db.scalar(sa.select(User).where(User.clerk_user_id == "user_forged")) is None


async def test_a_delivery_signed_with_the_wrong_secret_is_refused(
    client: AsyncClient, db: AsyncSession, webhook_secret: str
) -> None:
    other = "whsec_" + base64.b64encode(b"a-completely-different-secret!!!").decode()
    raw, headers = _signed({"type": "user.created", "data": {"id": "user_x"}}, secret=other)

    response = await client.post("/webhooks/clerk", content=raw, headers=headers)

    assert response.status_code == 401


async def test_a_signed_user_created_event_provisions_the_user(
    client: AsyncClient, db: AsyncSession, webhook_secret: str
) -> None:
    raw, headers = _signed(
        {
            "type": "user.created",
            "data": {
                "id": "user_hooked",
                "primary_email_address_id": "idn_1",
                "email_addresses": [{"id": "idn_1", "email_address": "hook@chessmark.test"}],
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
        }
    )

    response = await client.post("/webhooks/clerk", content=raw, headers=headers)

    assert response.status_code == 204
    db.expunge_all()
    user = await db.scalar(sa.select(User).where(User.clerk_user_id == "user_hooked"))
    assert user is not None
    assert user.email == "hook@chessmark.test"
    assert user.display_name == "Ada Lovelace"


async def test_an_update_does_not_erase_a_known_email(
    client: AsyncClient, db: AsyncSession, webhook_secret: str
) -> None:
    """Clerk omits fields it is not changing. Assigning them blindly would null out a known address
    on every partial update."""
    db.add(User(clerk_user_id="user_partial", email="known@chessmark.test"))
    await db.commit()

    raw, headers = _signed({"type": "user.updated", "data": {"id": "user_partial"}})
    await client.post("/webhooks/clerk", content=raw, headers=headers)

    db.expunge_all()
    user = await db.scalar(sa.select(User).where(User.clerk_user_id == "user_partial"))
    assert user is not None
    assert user.email == "known@chessmark.test"


async def test_a_delete_event_removes_the_user(
    client: AsyncClient, db: AsyncSession, webhook_secret: str
) -> None:
    db.add(User(clerk_user_id="user_doomed"))
    await db.commit()

    raw, headers = _signed({"type": "user.deleted", "data": {"id": "user_doomed"}})
    response = await client.post("/webhooks/clerk", content=raw, headers=headers)

    assert response.status_code == 204
    db.expunge_all()
    assert await db.scalar(sa.select(User).where(User.clerk_user_id == "user_doomed")) is None


async def test_an_unknown_event_type_is_accepted_and_ignored(
    client: AsyncClient, db: AsyncSession, webhook_secret: str
) -> None:
    """Clerk retries anything that is not 2xx, so refusing an event we do not care about creates an
    unbounded retry loop over a message we were never going to act on."""
    raw, headers = _signed({"type": "session.created", "data": {"id": "user_whatever"}})

    response = await client.post("/webhooks/clerk", content=raw, headers=headers)

    assert response.status_code == 204


async def test_the_webhook_refuses_everything_when_no_secret_is_set(
    client: AsyncClient, db: AsyncSession, monkeypatch: Any
) -> None:
    """Fail closed. An endpoint that accepts anything because nobody configured it is worse than
    one that is switched off."""
    from chessmark.core.config import get_settings

    monkeypatch.setattr(get_settings(), "clerk_webhook_secret", "")
    raw, headers = _signed({"type": "user.created", "data": {"id": "user_nosecret"}})

    response = await client.post("/webhooks/clerk", content=raw, headers=headers)

    assert response.status_code == 401

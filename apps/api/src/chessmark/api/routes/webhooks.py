"""Clerk webhooks (AUTH-01).

The only unauthenticated endpoint in the system that writes to `users`, which makes its signature
check the single thing standing between the internet and the account table. Verification happens
before the body is parsed, and the **raw** bytes are what get verified — re-serialising the JSON
would change whitespace and key order, and the signature would never match.

Users are also provisioned just-in-time on their first authenticated request (`db/users.py`), so
this endpoint is not on the critical path for signup. It exists for what a token cannot tell us:
a changed email, and a deleted account.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from chessmark.api.deps import SessionDep, SettingsDep
from chessmark.core.webhooks import WebhookError, verify
from chessmark.db.users import delete_by_clerk_id, upsert_user

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _primary_email(data: dict[str, Any]) -> str | None:
    """Clerk sends every address plus a pointer to the primary one."""
    primary_id = data.get("primary_email_address_id")
    for address in data.get("email_addresses") or []:
        if not isinstance(address, dict):
            continue
        if primary_id is None or address.get("id") == primary_id:
            value = address.get("email_address")
            if isinstance(value, str):
                return value
    return None


def _display_name(data: dict[str, Any]) -> str | None:
    parts = [data.get("first_name"), data.get("last_name")]
    name = " ".join(part for part in parts if isinstance(part, str) and part).strip()
    return name or (data.get("username") if isinstance(data.get("username"), str) else None)


@router.post("/clerk", status_code=status.HTTP_204_NO_CONTENT)
async def clerk_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[str | None, Header(alias="svix-timestamp")] = None,
    svix_signature: Annotated[str | None, Header(alias="svix-signature")] = None,
) -> None:
    """Handle a Clerk user event.

    Unknown event types are accepted and ignored rather than refused: Clerk retries anything that
    is not a 2xx, so rejecting an event we simply do not care about would produce an unbounded
    retry loop over a message we were never going to act on.
    """
    body = await request.body()

    try:
        verify(
            settings.clerk_webhook_secret,
            body=body,
            message_id=svix_id,
            timestamp=svix_timestamp,
            signature_header=svix_signature,
        )
    except WebhookError as error:
        # One message for every failure mode. Distinguishing "bad timestamp" from "bad signature"
        # tells an attacker tuning a forgery which half they have already solved.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature."
        ) from error

    payload = await request.json()
    event = payload.get("type")
    data = payload.get("data") or {}
    clerk_user_id = data.get("id")

    if not isinstance(clerk_user_id, str) or not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Event carries no user id."
        )

    if event in {"user.created", "user.updated"}:
        await upsert_user(
            session,
            clerk_user_id=clerk_user_id,
            email=_primary_email(data),
            display_name=_display_name(data),
        )
        await session.commit()
    elif event == "user.deleted":
        await delete_by_clerk_id(session, clerk_user_id)
        await session.commit()

"""Asking Clerk who someone is (AUTH-14).

**A session token does not carry an email.** Clerk's default token has `sub`, `iss`, `exp` and
little else, and `Principal.from_claims` reads an `email` claim that is only present if the
instance was configured to send one. Ours was not, so every `users` row held `email = NULL` and an
administrator granting credits had nothing to identify a person by but an opaque `user_...` id.

Three ways to fix that, and this is the one that needs no configuration and works retroactively:
ask Clerk's API with the secret key we already hold. Adding the claim to the session token is
cheaper at runtime and worth doing as well — this then simply never fires.

Deliberately best-effort. Identity is useful, not load-bearing: a lookup that fails must cost a
person nothing, because it happens on the request that provisions them. Every function here
returns `None` rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_ROOT = "https://api.clerk.com/v1"

#: Short on purpose. This runs inside a user's first request, and a slow identity service must not
#: become a slow sign-in — we would rather have no email than a hung request.
TIMEOUT_SECONDS = 5.0


def _primary_email(payload: dict[str, Any]) -> str | None:
    """The address Clerk considers primary, falling back to the first it lists."""
    addresses = payload.get("email_addresses") or []
    if not isinstance(addresses, list) or not addresses:
        return None

    primary_id = payload.get("primary_email_address_id")
    for entry in addresses:
        if isinstance(entry, dict) and entry.get("id") == primary_id:
            found = entry.get("email_address")
            return found if isinstance(found, str) else None

    first = addresses[0]
    if isinstance(first, dict):
        found = first.get("email_address")
        return found if isinstance(found, str) else None
    return None


def _display_name(payload: dict[str, Any]) -> str | None:
    parts = [payload.get("first_name"), payload.get("last_name")]
    name = " ".join(part for part in parts if isinstance(part, str) and part).strip()
    if name:
        return name
    username = payload.get("username")
    return username if isinstance(username, str) and username else None


class ClerkDirectory:
    """Reads Clerk's user directory with the server-held secret key.

    The key never leaves the API tier, the same rule that governs provider keys (invariant 10).
    """

    def __init__(self, secret_key: str) -> None:
        self._secret = secret_key

    @property
    def configured(self) -> bool:
        return bool(self._secret)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        if not self.configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.get(
                    f"{API_ROOT}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._secret}"},
                )
            if response.status_code != httpx.codes.OK:
                log.warning("clerk lookup %s answered %s", path, response.status_code)
                return None
            return response.json()
        except Exception:
            log.warning("clerk lookup %s failed", path, exc_info=True)
            return None

    async def identity_of(self, clerk_user_id: str) -> tuple[str | None, str | None]:
        """`(email, display_name)` for a Clerk user, or `(None, None)`."""
        payload = await self._get(f"/users/{clerk_user_id}")
        if not isinstance(payload, dict):
            return None, None
        return _primary_email(payload), _display_name(payload)

    async def find_by_email(self, email: str) -> str | None:
        """The Clerk user id for an address, or `None`.

        This is what lets an administrator grant credits to someone who has **never signed in**:
        our `users` row is created on a person's first request, so without asking Clerk there is
        nothing to grant to until they show up. Pre-granting is exactly what a private beta needs.
        """
        payload = await self._get("/users", {"email_address": [email], "limit": 2})
        if not isinstance(payload, list) or len(payload) != 1:
            # Zero is nobody; more than one is ambiguous, and guessing which person an operator
            # meant is the wrong way to be helpful about someone else's credits.
            return None
        found = payload[0]
        identifier = found.get("id") if isinstance(found, dict) else None
        return identifier if isinstance(identifier, str) else None


_directory: ClerkDirectory | None = None


def get_directory() -> ClerkDirectory:
    """One Clerk directory client per process.

    Lives here rather than in `api/deps` because `db.users.resolve_user` needs it too, and `db/`
    importing `api/` would invert the layering for the sake of one cached object.
    """
    global _directory
    if _directory is None:
        from chessmark.core.config import get_settings

        _directory = ClerkDirectory(get_settings().clerk_secret_key)
    return _directory


def reset_directory() -> None:
    """Drop the cached client. For tests and config reloads."""
    global _directory
    _directory = None

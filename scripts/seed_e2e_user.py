#!/usr/bin/env python3
"""Ensure the browser suite's test account exists, in Clerk and here, with credits to spend.

    make seed-e2e-user

Two halves, because a person is two rows in two systems:

1. **In Clerk.** The address ends `+clerk_test@example.com`, which Clerk development instances
   treat specially: no mail is sent and the verification code is always 424242. That is what lets
   the suite sign in for real — through Clerk's own flow, against a real JWT — with **no password
   stored anywhere in this repository**.
2. **Here.** A user row is provisioned just-in-time on their first authenticated request, so it
   may not exist yet; the suite visits the site once before this runs. Credits are granted because
   new users deliberately get none (AUTH-11) — an unattended suite that could not start a game
   would be testing nothing.

Idempotent on both halves: an existing Clerk user is reused, and the balance is *topped up to* the
target rather than added to, so running the suite fifty times does not mint fifty grants.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.credits import grant  # noqa: E402
from chessmark.db.enums import CreditReason  # noqa: E402
from chessmark.db.models import User  # noqa: E402
from chessmark.db.session import dispose_engine, get_sessionmaker  # noqa: E402

E2E_EMAIL = "chessmark-e2e+clerk_test@example.com"
CLERK_API = "https://api.clerk.com/v1"


def clerk(path: str, secret: str, *, method: str = "GET", body: dict | None = None):
    request = urllib.request.Request(
        f"{CLERK_API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            # Clerk's edge answers `Python-urllib/3.x` with a 403 (error 1010) before the request
            # ever reaches the API. Naming ourselves is what gets through.
            "User-Agent": "chessmark-e2e-seed/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def ensure_clerk_user(secret: str) -> str:
    """The Clerk id of the test account, creating it if this instance has never seen it."""
    from urllib.parse import quote

    existing = clerk(f"/users?email_address={quote(E2E_EMAIL)}", secret)
    if existing:
        return str(existing[0]["id"])

    created = clerk(
        "/users",
        secret,
        method="POST",
        body={
            "email_address": [E2E_EMAIL],
            "first_name": "Chessmark",
            "last_name": "E2E",
            "skip_password_requirement": True,
        },
    )
    return str(created["id"])


async def top_up(clerk_user_id: str, target: int) -> int | None:
    """Bring the local balance up to `target`. None if the user row does not exist yet."""
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            user = await session.scalar(sa.select(User).where(User.clerk_user_id == clerk_user_id))
            if user is None:
                return None

            shortfall = target - user.credit_balance
            if shortfall > 0:
                await grant(
                    session,
                    user.id,
                    shortfall,
                    note="browser suite",
                    reason=CreditReason.ADMIN_GRANT,
                )
                await session.commit()
                return target
            return user.credit_balance
    finally:
        await dispose_engine()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credits", type=int, default=50, help="balance to top the account up to")
    args = parser.parse_args()

    secret = os.environ.get("CLERK_SECRET_KEY") or get_settings().clerk_secret_key
    if not secret:
        print("CLERK_SECRET_KEY is not set — the signed-in suite cannot run", file=sys.stderr)
        return 2

    try:
        clerk_user_id = ensure_clerk_user(secret)
    except urllib.error.HTTPError as error:
        print(f"Clerk refused: {error.code} {error.read().decode()[:400]}", file=sys.stderr)
        return 1

    balance = await top_up(clerk_user_id, args.credits)

    print(
        json.dumps({"email": E2E_EMAIL, "clerkUserId": clerk_user_id, "credits": balance}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

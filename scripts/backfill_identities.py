#!/usr/bin/env python3
"""Fill in the email and name of users provisioned before we asked Clerk for them (AUTH-14).

    make backfill-identities
    make backfill-identities ARGS=--dry-run

Identity is resolved at provisioning now, but rows created before that carry only an opaque
`user_...` id — which leaves an administrator granting credits with nothing to go on. This asks
Clerk once per such row and fills what it finds.

Safe to re-run: it only touches rows whose email is missing, and `upsert_user` coalesces, so a row
Clerk cannot identify is left exactly as it was rather than blanked.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from chessmark.core.clerk import ClerkDirectory  # noqa: E402
from chessmark.core.config import get_settings  # noqa: E402
from chessmark.db.models import User  # noqa: E402
from chessmark.db.session import dispose_engine, session_scope  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, and change nothing"
    )
    args = parser.parse_args()

    directory = ClerkDirectory(get_settings().clerk_secret_key)
    if not directory.configured:
        print("CLERK_SECRET_KEY is not set — nothing to ask", file=sys.stderr)
        return 2

    try:
        async with session_scope() as session:
            rows = list(
                await session.scalars(sa.select(User).where(User.email.is_(None)))
            )
            print(f"{len(rows)} user(s) without an email")

            filled = 0
            for user in rows:
                email, display_name = await directory.identity_of(user.clerk_user_id)
                if not email and not display_name:
                    print(f"  ? {user.clerk_user_id} — Clerk had nothing")
                    continue

                print(f"  {'would fill' if args.dry_run else 'filled'} {user.clerk_user_id} → {email}")
                if not args.dry_run:
                    user.email = email or user.email
                    user.display_name = display_name or user.display_name
                    filled += 1

            if args.dry_run:
                # Roll back rather than commit; the scope commits on a clean exit.
                await session.rollback()
                print("\ndry run — nothing written")
            else:
                print(f"\nfilled {filled} of {len(rows)}")
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

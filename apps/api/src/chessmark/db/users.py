"""User provisioning (AUTH-01).

Users arrive by two paths, and both are needed.

**Just-in-time, on the first authenticated request.** A verified token is already proof that Clerk
knows this person, so the row can be created from its claims. This is what makes the app work at
the moment someone signs up, rather than whenever the webhook happens to land.

**By webhook**, for everything the token cannot tell us: a changed email, a deleted account. A
token only arrives when the user shows up, so without the webhook a deletion would never reach us.

Relying on the webhook alone is the common mistake — it puts a third party's delivery latency in
front of a user's first action, and webhook delivery is retried, not instant.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.auth import Principal
from chessmark.db.models import User


async def upsert_user(
    session: AsyncSession,
    *,
    clerk_user_id: str,
    email: str | None = None,
    display_name: str | None = None,
) -> User:
    """Create or refresh a user, keyed on `clerk_user_id`.

    `COALESCE` on the updates rather than plain assignment: a Clerk session token often omits
    `email` (it is only present if the JWT template includes it), and letting an absent claim
    overwrite a known address with NULL would lose data on every login.
    """
    statement = (
        pg_insert(User)
        .values(clerk_user_id=clerk_user_id, email=email, display_name=display_name)
        .on_conflict_do_update(
            index_elements=[User.clerk_user_id],
            set_={
                "email": sa.func.coalesce(sa.literal(email), User.email),
                "display_name": sa.func.coalesce(sa.literal(display_name), User.display_name),
                "updated_at": sa.func.now(),
            },
        )
        .returning(User)
    )

    user: User = (await session.execute(statement)).scalar_one()
    return user


async def user_for(session: AsyncSession, principal: Principal) -> User:
    """The `users` row for a verified caller, creating it if this is their first request."""
    return await upsert_user(
        session,
        clerk_user_id=principal.clerk_user_id,
        email=principal.email,
        display_name=principal.display_name,
    )


async def get_by_clerk_id(session: AsyncSession, clerk_user_id: str) -> User | None:
    found: User | None = await session.scalar(
        sa.select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return found


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    found: User | None = await session.scalar(sa.select(User).where(User.id == user_id))
    return found


async def delete_by_clerk_id(session: AsyncSession, clerk_user_id: str) -> bool:
    """Remove a user Clerk says is gone.

    Games keep running: `players.user_id` is `ON DELETE SET NULL`, so the game record survives with
    the seat unattributed. Deleting an account must not delete public game history that other
    people's results are measured against.
    """
    result = await session.execute(sa.delete(User).where(User.clerk_user_id == clerk_user_id))
    return bool(result.rowcount)  # type: ignore[attr-defined]

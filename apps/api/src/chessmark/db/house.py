"""The house account: Chessmark's own balance, which pays for every game no person started
(ADR-0058).

Tournaments and operator games used to have no payer at all (ADR-0052). Their turns were free to
the ledger, but not to OpenRouter, where they drew on the same prepaid balance that backs every
dollar of credit users hold. Nothing tied the two together, so a busy tournament could spend
OpenRouter below what users held. Their credit would still read correctly, and their next turn
would hit a 402.

So the house is a user row like any other, and pays like one:
- a turn is charged to it;
- a game it cannot fund pauses, and resumes when it is funded;
- billing reconciliation settles its games.

Its positive balance counts toward the credit users hold in the sales headroom (ADR-0056), so
credit is never sold out from under a tournament, and a tournament never spends what users hold.

**No house row means no payer**, as before. The migration creates the row in every real database.
The test suite truncates it between cases, so tests that aren't about the house see the old rule.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.models import Game, User

#: The house row's `clerk_user_id`. Not a Clerk id: nobody signs in as the house. The colon keeps
#: it out of Clerk's own `user_…` namespace, so it can never collide with a real account.
HOUSE_CLERK_ID = "chessmark:house"


async def house_id(session: AsyncSession) -> uuid.UUID | None:
    """The house row's id, or `None` where it does not exist. One indexed lookup."""
    found: uuid.UUID | None = await session.scalar(
        sa.select(User.id).where(User.clerk_user_id == HOUSE_CLERK_ID)
    )
    return found


def payer(game: Game, house: uuid.UUID | None) -> uuid.UUID | None:
    """Who pays for this game's turns: the person who started it, or else the house."""
    return game.created_by_user_id or house


async def payer_of(session: AsyncSession, game: Game) -> uuid.UUID | None:
    """`payer`, looking the house up only for a game no person started."""
    if game.created_by_user_id is not None:
        return game.created_by_user_id
    return await house_id(session)


__all__ = ["HOUSE_CLERK_ID", "house_id", "payer", "payer_of"]

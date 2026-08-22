"""Per-user daily quotas — layer 2 of ADR-0011 (AUTH-03).

Two numbers per user per day: games started and dollars spent. Either can refuse a new game.

**The reservation is a single statement, not a read followed by a write.** `SELECT` the count,
compare it, then `UPDATE` is the obvious shape and it is wrong: two requests from the same user
arriving together both read the old count, both find room, and both proceed. That is not a
theoretical race — it is the shape of the attack, since firing concurrent requests is exactly what
someone trying to exceed a quota would do. Postgres decides it instead, via an
`INSERT ... ON CONFLICT DO UPDATE` whose `WHERE` clause is the quota check: the row is returned
only if the update actually happened.

The day is **UTC** for the same reason the global counter's is — see `core/budget.py`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.budget import today
from chessmark.db.models import UsageLedger


@dataclass(frozen=True, slots=True)
class Usage:
    day: dt.date
    games_started: int
    usd_spent: Decimal


class QuotaExceededError(Exception):
    """The user has used up today's allowance.

    Carries the numbers so the API can say *which* limit and *when it resets* — "quota exceeded"
    with no further detail is a dead end for the person reading it.
    """

    def __init__(self, *, reason: str, used: int | Decimal, limit: int | Decimal) -> None:
        super().__init__(f"daily {reason} quota reached ({used} of {limit})")
        self.reason = reason
        self.used = used
        self.limit = limit


async def usage_for(
    session: AsyncSession, user_id: uuid.UUID, *, day: dt.date | None = None
) -> Usage:
    """Today's counters, whether or not a row exists yet."""
    when = day or today()
    row = await session.scalar(
        sa.select(UsageLedger).where(UsageLedger.user_id == user_id, UsageLedger.day == when)
    )
    if row is None:
        return Usage(day=when, games_started=0, usd_spent=Decimal(0))
    return Usage(day=when, games_started=row.games_started, usd_spent=row.usd_spent)


async def reserve_game(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    max_games: int,
    max_usd: Decimal | None = None,
    day: dt.date | None = None,
) -> Usage:
    """Claim one game against today's quota, or raise `QuotaExceededError`.

    Reserved *before* the game is created, not after. Counting on completion would let a user open
    any number of games at once and only discover the quota when the money was already committed.
    """
    when = day or today()

    # The spend limit cannot be enforced in the same statement — `usd_spent` is updated by workers
    # as calls complete, so it is read here and compared before the reservation is attempted.
    # A user who blows through it mid-game is stopped at their *next* game, which is the most a
    # daily-spend limit can promise: the cost of a call is not known until it returns.
    if max_usd is not None and max_usd > 0:
        current = await usage_for(session, user_id, day=when)
        if current.usd_spent >= max_usd:
            raise QuotaExceededError(reason="spend", used=current.usd_spent, limit=max_usd)

    statement = (
        pg_insert(UsageLedger)
        .values(user_id=user_id, day=when, games_started=1, usd_spent=Decimal(0))
        .on_conflict_do_update(
            index_elements=[UsageLedger.user_id, UsageLedger.day],
            set_={"games_started": UsageLedger.games_started + 1},
            # The quota check *is* the where clause. If it fails, no row is updated and nothing is
            # returned — so two concurrent requests cannot both find room in the last slot.
            where=UsageLedger.games_started < max_games,
        )
        .returning(UsageLedger.games_started, UsageLedger.usd_spent)
    )

    row = (await session.execute(statement)).one_or_none()
    if row is None:
        used = await usage_for(session, user_id, day=when)
        raise QuotaExceededError(reason="games", used=used.games_started, limit=max_games)

    return Usage(day=when, games_started=row.games_started, usd_spent=row.usd_spent)


async def record_spend(
    session: AsyncSession,
    user_id: uuid.UUID,
    usd: Decimal,
    *,
    day: dt.date | None = None,
) -> Decimal:
    """Add to today's spend and return the new total.

    Upserts, because spend can be recorded for a user whose ledger row does not exist yet — a game
    started yesterday can still be spending money today.
    """
    when = day or today()

    statement = (
        pg_insert(UsageLedger)
        .values(user_id=user_id, day=when, games_started=0, usd_spent=usd)
        .on_conflict_do_update(
            index_elements=[UsageLedger.user_id, UsageLedger.day],
            set_={"usd_spent": UsageLedger.usd_spent + usd},
        )
        .returning(UsageLedger.usd_spent)
    )

    total: Decimal = (await session.execute(statement)).scalar_one()
    return total


async def reset_quota(
    session: AsyncSession, user_id: uuid.UUID, *, day: dt.date | None = None
) -> None:
    """Clear a user's counters for a day (AUTH-08).

    An admin action, not a user-facing one — it exists for the case where our own bug consumed
    someone's allowance.
    """
    await session.execute(
        sa.delete(UsageLedger).where(
            UsageLedger.user_id == user_id, UsageLedger.day == (day or today())
        )
    )

"""Two buyers cannot both take the last of the headroom (ADR-0056).

At the database, with two real sessions, because the rule is about what one transaction can see of
another's uncommitted work: a reservation inserted but not yet committed is invisible to a second
buyer's headroom check, so without the lock both would pass. The HTTP-level race in
`test_buy_credit.py` cannot show that — its requests never actually overlap.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.core.credit_pricing import quote
from chessmark.db.capacity import NoHeadroomError, reserve
from chessmark.db.models import User

pytestmark = pytest.mark.integration

#: $5 of credit can be sold ($15 at OpenRouter, $10 kept back); one $5 purchase is $3.79 of it.
REMAINING = Decimal(15)
RESERVE = Decimal(10)


async def test_a_second_reservation_waits_for_the_first_and_then_sees_it(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    first, second = User(clerk_user_id="user_first"), User(clerk_user_id="user_second")
    db.add_all([first, second])
    await db.commit()
    first_id, second_id = first.id, second.id

    async with sessionmaker() as a, sessionmaker() as b:
        await reserve(
            a, first_id, quote(Decimal(5)), openrouter_remaining=REMAINING, house_reserve=RESERVE
        )

        waiting = asyncio.create_task(
            reserve(
                b,
                second_id,
                quote(Decimal(5)),
                openrouter_remaining=REMAINING,
                house_reserve=RESERVE,
            )
        )
        await asyncio.sleep(0.5)
        assert not waiting.done(), "the second buyer checked the headroom while the first held it"

        await a.commit()
        with pytest.raises(NoHeadroomError):
            await waiting
        await b.rollback()

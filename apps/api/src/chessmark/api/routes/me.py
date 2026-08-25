"""The signed-in caller (AUTH-10).

Exists so the frontend can show a balance *before* it is spent. Discovering that you cannot afford
a game by being refused is a bad way to learn it, particularly when the model you picked is what
decided the price.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter

from chessmark.api.deps import CurrentUser, SessionDep
from chessmark.api.schemas import MeOut
from chessmark.db.quotas import usage_for

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeOut)
async def get_me(session: SessionDep, user: CurrentUser) -> MeOut:
    usage = await usage_for(session, user.id)

    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        # A balance, not a daily remainder (ADR-0016). It does not refill, so a reader who sees
        # zero needs to know that asking is the only way it changes — hence the copy in the UI.
        credit_balance=user.credit_balance,
        games_started_today=usage.games_started,
        usd_spent_today=Decimal(usage.usd_spent),
    )

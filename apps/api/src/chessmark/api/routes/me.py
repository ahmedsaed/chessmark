"""The signed-in caller (AUTH-03).

Exists so the frontend can show a quota *before* it is hit. Discovering your daily limit by being
refused is a bad way to learn it, particularly when the refusal costs you the game you were trying
to start.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter

from chessmark.api.deps import CurrentUser, SessionDep, SettingsDep
from chessmark.api.schemas import MeOut
from chessmark.db.quotas import usage_for

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeOut)
async def get_me(session: SessionDep, settings: SettingsDep, user: CurrentUser) -> MeOut:
    usage = await usage_for(session, user.id)
    limit = settings.max_games_per_user_per_day

    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        games_started_today=usage.games_started,
        games_remaining_today=max(0, limit - usage.games_started),
        usd_spent_today=Decimal(usage.usd_spent),
    )

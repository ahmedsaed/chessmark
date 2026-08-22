"""Admin surface (AUTH-08).

Three things an operator needs when something is going wrong at two in the morning: what is being
spent, how to stop a specific game, and how to give someone back an allowance our own bug consumed.

Every route requires `users.is_admin`, which is set in the database by hand. There is deliberately
no endpoint that grants it: an admin surface that can promote its own callers is one compromised
session away from being everyone's admin surface.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status

from chessmark.api.deps import AdminUser, BudgetDep, SessionDep, SettingsDep
from chessmark.api.schemas import AdminSpend, AdminUsage
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, User
from chessmark.db.quotas import reset_quota, usage_for
from chessmark.db.repositories import get_game
from chessmark.game import GameResult, Termination

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/spend", response_model=AdminSpend)
async def get_spend(
    session: SessionDep, budget: BudgetDep, settings: SettingsDep, admin: AdminUser
) -> AdminSpend:
    """Today's spend against the global limit, and what it has been spent on.

    The Redis counter is the authority for *tripping* the switch, but the sum over `games` is the
    authority for *accounting* — they are reported side by side on purpose, because a gap between
    them means one of the two is wrong and an operator should be able to see it.
    """
    del admin

    total_games = await session.scalar(sa.select(sa.func.count()).select_from(Game)) or 0
    running = (
        await session.scalar(
            sa.select(sa.func.count()).select_from(Game).where(Game.status == GameStatus.RUNNING)
        )
        or 0
    )
    recorded = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Game.total_cost_usd), 0))
    )

    return AdminSpend(
        spent_today_usd=await budget.spent_today(),
        daily_limit_usd=budget.limit_usd,
        remaining_usd=await budget.remaining_usd(),
        tripped=await budget.tripped(),
        lifetime_recorded_usd=Decimal(recorded or 0),
        games_total=int(total_games),
        games_running=int(running),
    )


@router.get("/users/{user_id}/usage", response_model=AdminUsage)
async def get_user_usage(session: SessionDep, user_id: uuid.UUID, admin: AdminUser) -> AdminUsage:
    del admin
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user.")

    usage = await usage_for(session, user_id)
    return AdminUsage(
        user_id=user_id,
        day=usage.day,
        games_started=usage.games_started,
        usd_spent=usage.usd_spent,
    )


@router.post("/users/{user_id}/usage/reset", response_model=AdminUsage)
async def reset_user_quota(session: SessionDep, user_id: uuid.UUID, admin: AdminUser) -> AdminUsage:
    """Give back today's allowance. For when our bug consumed it, not as a favour."""
    del admin
    if await session.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user.")

    await reset_quota(session, user_id)
    await session.commit()

    usage = await usage_for(session, user_id)
    return AdminUsage(
        user_id=user_id,
        day=usage.day,
        games_started=usage.games_started,
        usd_spent=usage.usd_spent,
    )


@router.post("/games/{game_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_game(session: SessionDep, game_id: uuid.UUID, admin: AdminUser) -> None:
    """Stop a game that is spending money it should not be.

    Recorded as **aborted**, never as a loss for either side. An operator's intervention is not a
    chess result, and letting one into the record would corrupt exactly the number this project
    exists to publish.
    """
    del admin
    game = await get_game(session, game_id)

    if game.status is not GameStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Game is {game.status.value}, not running.",
        )

    game.status = GameStatus.ABORTED
    game.result = GameResult.ONGOING
    game.termination = Termination.ADJUDICATION
    game.termination_detail = "Cancelled by an administrator."
    game.ended_at = sa.func.now()
    await session.commit()

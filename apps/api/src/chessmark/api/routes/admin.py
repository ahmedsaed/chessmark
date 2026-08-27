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
from chessmark.api.schemas import (
    AdminSpend,
    AdminUsage,
    CreditEntryOut,
    CreditGrantOut,
    CreditGrantRequest,
)
from chessmark.db.credits import grant, history_of
from chessmark.db.enums import GameStatus
from chessmark.db.models import Game, User
from chessmark.db.quotas import reset_quota, usage_for
from chessmark.db.repositories import get_game
from chessmark.db.users import resolve_user
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


async def _resolve_user(session: SessionDep, identifier: str) -> User:
    """`db.users.resolve_user`, with a 404 instead of a `None`."""
    found = await resolve_user(session, identifier)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No user matching {identifier!r}. Give an email address, a Clerk user id, "
                "or a Chessmark user id."
            ),
        )
    return found


@router.post("/credits", response_model=CreditGrantOut)
async def grant_credits(
    session: SessionDep,
    request: CreditGrantRequest,
    admin: AdminUser,
) -> CreditGrantOut:
    """Give a user credits, or take them back (AUTH-11, AUTH-13, ADR-0016).

    The only way a balance goes up. New accounts hold zero and there is no request flow in the
    product, so this is the whole granting mechanism during the testing phase — deliberately, to
    keep an unattended account from consuming provider spend.

    `user` is whatever you have: an email, a Clerk id, or ours. It used to be our internal UUID
    alone, which meant granting credits began with a database query.

    A negative `credits` removes them, clamped at zero: a negative balance would be a debt to work
    off before playing again, which is not what anyone means by taking credits away.

    Every movement is recorded against the administrator who made it (AUTH-13), so a balance can be
    explained afterwards rather than merely observed.
    """
    user = await _resolve_user(session, request.user)

    balance = await grant(
        session, user.id, request.credits, actor_user_id=admin.id, note=request.note
    )
    await session.commit()

    return CreditGrantOut(
        user_id=user.id, email=user.email, credit_balance=balance, granted=request.credits
    )


@router.get("/users/{user_id}/credits", response_model=list[CreditEntryOut])
async def credit_history(
    session: SessionDep, user_id: uuid.UUID, admin: AdminUser
) -> list[CreditEntryOut]:
    """How a balance got to where it is (AUTH-13)."""
    del admin
    return [CreditEntryOut.from_model(row) for row in await history_of(session, user_id)]


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

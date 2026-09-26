"""Pausing and resuming a game by the person paying for it (ADR-0052).

A game somebody starts spends their credit turn by turn, so they can stop it spending: **pause**
holds it before its next turn, **resume** plays on. Only a game between two models — in a game a
person plays, the model moves only after they do, so they already hold the pace.

Nothing here ends a game. A pause is not a result, it is not the model's doing, and a game held by
its owner is kept off the abandonment clock like a halt (`worker._is_our_stop`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.core.pause_requests import PauseRequests
from chessmark.db.credits import InsufficientCreditError, balance_of, can_play
from chessmark.db.enums import GameStatus, PlayerKind
from chessmark.db.models import Game, GameEvent, Player
from chessmark.db.repositories import (
    GameInFlightError,
    get_game,
    load_events,
    rebuild_referee,
)
from chessmark.orchestration.match import model_for
from chessmark.orchestration.queue import AdvanceTurn
from chessmark.orchestration.reconciler import resume as resume_paused
from chessmark.orchestration.worker import OWNER_PAUSE, TERMINAL_STATUSES, hold_for_owner


class NotYourGameError(Exception):
    """The caller did not start this game, so it is not theirs to pause."""


class CannotPauseError(Exception):
    """A game that cannot be paused by its owner — ended, or one they are playing themselves."""


class CannotResumeError(Exception):
    """A game its owner did not pause. What holds it is not theirs to lift."""


@dataclass(slots=True)
class OwnerAction:
    status: GameStatus
    #: True when the pause is requested and waits for the turn in progress to finish.
    pausing: bool = False
    #: Committed by the caller, then published.
    events: list[GameEvent] = field(default_factory=list)
    #: The turn to enqueue after the commit, for a resume.
    job: AdvanceTurn | None = None


async def _players(session: AsyncSession, game: Game) -> list[Player]:
    return list(await session.scalars(sa.select(Player).where(Player.game_id == game.id)))


async def _check(session: AsyncSession, game: Game, user_id: uuid.UUID) -> list[Player]:
    if game.created_by_user_id != user_id:
        raise NotYourGameError("Only the person who started this game can pause it.")
    players = await _players(session, game)
    if any(PlayerKind(p.kind) is PlayerKind.HUMAN for p in players):
        raise CannotPauseError(
            "You are playing this game: the model moves only after you do, so it spends nothing "
            "while you wait."
        )
    return players


async def pause(
    session: AsyncSession, requests: PauseRequests, game: Game, user_id: uuid.UUID
) -> OwnerAction:
    """Stop a game spending, before its next turn."""
    players = await _check(session, game, user_id)
    if game.status in TERMINAL_STATUSES:
        raise CannotPauseError("This game has ended.")
    if game.pause_reason == OWNER_PAUSE:
        return OwnerAction(status=game.status)

    if game.status is GameStatus.PAUSED:
        # Paused for something else — a provider, credit, a halt. No turn is in flight, so it is
        # held for its owner now, and the reconciler will not resume it when that other wait ends.
        try:
            game = await get_game(session, game.id, claim=True)
        except GameInFlightError:
            # The reconciler is resuming it this instant; the worker takes the request instead.
            await requests.request(game.id)
            return OwnerAction(status=game.status, pausing=True)

        referee = await rebuild_referee(session, game)
        to_move = next(p for p in players if p.colour == referee.side_to_move)
        before = game.event_seq
        await hold_for_owner(session, game, to_move)
        return OwnerAction(
            status=GameStatus.PAUSED,
            events=await load_events(session, game.id, after_seq=before),
        )

    # Running: a turn may be holding the row, so the request waits for the worker (see
    # `core.pause_requests` for why it is not written to the game directly).
    await requests.request(game.id)
    return OwnerAction(status=game.status, pausing=True)


async def resume(
    session: AsyncSession, requests: PauseRequests, game: Game, user_id: uuid.UUID
) -> OwnerAction:
    """Play on. Needs credit if either seat costs anything."""
    players = await _check(session, game, user_id)

    if game.status is GameStatus.RUNNING:
        # A pause asked for and not yet reached is simply withdrawn.
        await requests.clear(game.id)
        return OwnerAction(status=game.status)

    if game.pause_reason != OWNER_PAUSE:
        raise CannotResumeError("This game is not paused by you.")

    paid = any(not model_for(p).endswith(":free") for p in players)
    if paid and not await can_play(session, user_id):
        raise InsufficientCreditError(held=await balance_of(session, user_id))

    await requests.clear(game.id)
    before = game.event_seq
    job = await resume_paused(session, game)
    return OwnerAction(
        status=GameStatus.RUNNING,
        events=await load_events(session, game.id, after_seq=before),
        job=job,
    )


__all__ = [
    "CannotPauseError",
    "CannotResumeError",
    "NotYourGameError",
    "OwnerAction",
    "pause",
    "resume",
]

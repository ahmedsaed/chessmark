"""Shared test helpers for anything that needs a real game running.

Lives here rather than in a package conftest because both the orchestration tests and the API
tests drive real games, and duplicating the setup would let the two drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import alternating
from chessmark.db.enums import PlayerKind
from chessmark.db.models import Game, Player, User
from chessmark.game import Colour
from chessmark.orchestration.match import Match, Seat, create_match, start_match
from chessmark.orchestration.queue import AdvanceTurn, TurnQueue
from chessmark.orchestration.worker import TurnWorker


@dataclass(slots=True)
class Fixture:
    match: Match
    first_job: AdvanceTurn
    queue: TurnQueue

    @property
    def game(self) -> Game:
        return self.match.game

    @property
    def white(self) -> Player:
        return self.match.white

    @property
    def black(self) -> Player:
        return self.match.black


async def seat_match(db: AsyncSession, queue: TurnQueue, **kwargs: Any) -> Fixture:
    """Create a match, start it, and enqueue its first turn — the real startup path."""
    match = await create_match(
        db,
        white=Seat(display_name="white-model", model="scripted/white"),
        black=Seat(display_name="black-model", model="scripted/black"),
        **kwargs,
    )
    job = await start_match(db, queue, game_id=match.game.id)
    await db.commit()
    await queue.enqueue(job)

    return Fixture(match=match, first_job=job, queue=queue)


async def make_user(db: AsyncSession, clerk_user_id: str = "user_human") -> User:
    user = User(
        clerk_user_id=clerk_user_id,
        email=f"{clerk_user_id}@chessmark.test",
        display_name=clerk_user_id,
    )
    db.add(user)
    await db.flush()
    return user


async def seat_human_match(
    db: AsyncSession,
    queue: TurnQueue,
    *,
    user: User,
    human_colour: Colour = Colour.WHITE,
    **kwargs: Any,
) -> Fixture:
    """A person against a scripted model, started the way the endpoint starts one."""
    you = Seat(display_name="you", kind=PlayerKind.HUMAN, user_id=user.id)
    machine = Seat(display_name="black-model", model="scripted/black")
    white, black = (you, machine) if human_colour is Colour.WHITE else (machine, you)

    match = await create_match(db, white=white, black=black, **kwargs)
    job = await start_match(db, queue, game_id=match.game.id)
    await db.commit()
    await queue.enqueue(job)

    return Fixture(match=match, first_job=job, queue=queue)


def both_sides(
    white_moves: list[str], black_moves: list[str], *, cost: float = 0.0
) -> Callable[..., Any]:
    """One completion function serving both players, chosen by whose transcript is being sent."""
    return alternating(white_moves, black_moves, cost=cost)


async def run_next(worker: TurnWorker, queue: TurnQueue, *, consumer: str = "test-worker") -> Any:
    """Consume and process one job — exactly what the worker loop does.

    Tests that inspect the queue afterwards must go through this rather than calling `handle`
    directly, or the job the fixture enqueued is still sitting in the stream and the assertion
    reads it instead of whatever the turn produced.
    """
    deliveries = await queue.consume(consumer, block_ms=500)
    if not deliveries:
        return None
    return await worker.process(deliveries[0])


async def drain(queue: TurnQueue, *, consumer: str = "drain") -> int:
    """Consume and ack everything currently queued."""
    drained = 0
    while True:
        deliveries = await queue.consume(consumer, block_ms=100)
        if not deliveries:
            return drained
        for delivery in deliveries:
            await queue.ack(delivery.message_id)
            drained += 1

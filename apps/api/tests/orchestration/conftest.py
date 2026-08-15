"""Fixtures for orchestration tests.

Everything here drives the *real* worker against a real Redis and a real Postgres, replacing only
the provider. The properties under test — idempotency, crash resumption, ack-after-commit — are
properties of the interaction between those pieces, so mocking any of them would test nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.agents.llm import LlmGateway
from chessmark.agents.scripted import plays
from chessmark.agents.turn import TurnLimits
from chessmark.db.models import Game, Player
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


@pytest.fixture
async def queue(redis: Any) -> TurnQueue:
    turn_queue = TurnQueue(redis, stream="test:turns", group="test-workers")
    await turn_queue.ensure_group()
    return turn_queue


@pytest.fixture
def make_worker(
    sessionmaker: async_sessionmaker[AsyncSession], queue: TurnQueue, redis: Any
) -> Callable[..., TurnWorker]:
    def _make(
        completion_fn: Any,
        *,
        limits: TurnLimits | None = None,
        consumer: str = "test-worker",
        publish: bool = False,
    ) -> TurnWorker:
        return TurnWorker(
            sessionmaker=sessionmaker,
            queue=queue,
            gateway=LlmGateway(completion_fn=completion_fn),
            redis=redis if publish else None,
            limits=limits,
            consumer=consumer,
        )

    return _make


async def seat_match(
    db: AsyncSession,
    queue: TurnQueue,
    **kwargs: Any,
) -> Fixture:
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


@pytest.fixture
async def game(db: AsyncSession, queue: TurnQueue) -> Fixture:
    return await seat_match(db, queue)


def both_sides(white_moves: list[str], black_moves: list[str]) -> Callable[..., Any]:
    """One completion function serving both players, chosen by whose transcript is being sent.

    The worker gives each side its own gateway call, but a test only injects one function — so it
    decides from the system prompt which player is asking.
    """
    white = plays(white_moves)
    black = plays(black_moves)

    async def _complete(**kwargs: Any) -> Any:
        system = str(kwargs.get("messages", [{}])[0].get("content", ""))
        chooser = white if "as white" in system.lower() else black
        return await chooser(**kwargs)

    return _complete


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

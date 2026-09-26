"""A turn interrupted twice keeps what it did both times (ADR-0045).

Found by reconciling a game against OpenRouter's own record of it. Game `c2fd378a` spent 24 hours at
one ply: every resume of its interrupted turn got a clean answer from Poolside — status 200,
`tool_calls`, 8,461 prompt tokens each time — and then a 429 on the next round. OpenRouter holds 76
of those answers. We hold none: the turn's call count stayed at 2 for the whole day, and each resume
sent the identical prompt, having thrown the previous one's round away.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import step, tool_call
from chessmark.db.enums import TurnStatus
from chessmark.db.models import LlmCall, Turn
from tests.orchestration.test_pause_on_rate_limit import SharedPoolError
from tests.support import Fixture

pytestmark = pytest.mark.integration


class AnswersOncePerAttempt:
    """One good round, then a shared-pool 429 for as long as it is asked — every attempt, as the
    Poolside endpoint did. `next_attempt` is the worker picking the game up again."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answered = False

    def next_attempt(self) -> None:
        self.answered = False

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.answered:
            self.answered = True
            return step(tool_call("get_board", call_id=f"call_board_{len(self.calls)}"))
        raise SharedPoolError


async def test_each_attempt_keeps_the_round_it_completed(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any
) -> None:
    complete = AnswersOncePerAttempt()
    worker = make_worker(complete)

    await worker.handle(game.first_job)  # attempt 1: one round, then refused
    await _reopen(sessionmaker, game)
    complete.next_attempt()
    answered_first = list(complete.calls)
    await worker.handle(game.first_job)  # attempt 2 resumes it: one more round, then refused

    async with sessionmaker() as session:
        turns = list(await session.scalars(sa.select(Turn).where(Turn.player_id == game.white.id)))
        kept = await session.scalar(
            sa.select(sa.func.count()).select_from(LlmCall).where(LlmCall.error.is_(None))
        )

    assert [t.status for t in turns] == [TurnStatus.INTERRUPTED]
    # Both answered rounds were paid for, so both are on the record.
    assert kept == 2, f"kept {kept} of the 2 answered rounds"
    # And the second attempt continued the first rather than resending it.
    first = complete.calls[0]["messages"]
    second = complete.calls[len(answered_first)]["messages"]
    assert len(second) > len(first), "the resume sent the same prompt again"


async def _reopen(sessionmaker: Any, game: Fixture) -> None:
    """What the reconciler does when the pause is over: the game runs again."""
    from chessmark.db.models import Game
    from chessmark.orchestration.reconciler import resume

    async with sessionmaker() as session, session.begin():
        stored = await session.get(Game, game.game.id)
        assert stored is not None
        await resume(session, stored)


# ====================================================================== every failure keeps them


def _answers_then(failure: Exception) -> Any:
    """One answered round, then `failure` raised from inside the turn loop."""
    state = {"calls": 0}

    async def complete(**_kwargs: Any) -> Any:
        state["calls"] += 1
        if state["calls"] == 1:
            return step(tool_call("get_board"))
        raise failure

    return complete


class RejectedError(Exception):
    """A 400: the endpoint refused the request itself."""

    status_code = 400

    def __init__(self) -> None:
        super().__init__("litellm.BadRequestError: OpenrouterException - invalid request")


async def test_a_rejected_request_keeps_the_round_before_it(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any
) -> None:
    """ADR-0053. It used to roll the whole turn back — the answered round with it — and the worker
    then abandoned the game with nothing on the record of what it had paid for."""
    await make_worker(_answers_then(RejectedError())).handle(game.first_job)

    async with sessionmaker() as session:
        answered = await session.scalar(
            sa.select(sa.func.count()).select_from(LlmCall).where(LlmCall.error.is_(None))
        )
        statuses = list(await session.scalars(sa.select(Turn.status)))

    assert answered == 1
    assert statuses == [TurnStatus.INTERRUPTED]


async def test_a_mangled_tool_call_keeps_the_rounds_and_the_mangled_answer(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any
) -> None:
    """The endpoint garbled a tool call the model made. Both answers were billed; both are kept."""
    from chessmark.agents.scripted import scripted

    mangled = step(content='<tool_call>{"name": "make_move", "arguments": {"move": "e4"}}')
    await make_worker(scripted(step(tool_call("get_board")), mangled)).handle(game.first_job)

    async with sessionmaker() as session:
        calls = await session.scalar(sa.select(sa.func.count()).select_from(LlmCall))
        statuses = list(await session.scalars(sa.select(Turn.status)))

    assert calls == 2
    assert statuses == [TurnStatus.INTERRUPTED]


async def test_a_failure_after_the_move_leaves_the_move_standing(
    db: AsyncSession, game: Fixture, make_worker: Any, sessionmaker: Any
) -> None:
    """A turn goes on past its move until the model stops (ADR-0037), so the closing round can be
    the one that fails. Kept as interrupted, such a turn would be resumed for a *later* ply — one
    row holding two prompts and two moves. It is a completed turn: the move stands."""
    from chessmark.db.models import Game

    state = {"calls": 0}

    async def complete(**_kwargs: Any) -> Any:
        state["calls"] += 1
        if state["calls"] == 1:
            return step(tool_call("make_move", move="e4"))
        raise RejectedError  # the closing round, after the ply is committed

    await make_worker(complete).handle(game.first_job)

    async with sessionmaker() as session:
        stored = await session.get(Game, game.game.id)
        statuses = list(await session.scalars(sa.select(Turn.status)))

    assert stored is not None
    assert stored.ply_count == 1
    assert statuses == [TurnStatus.COMPLETED]

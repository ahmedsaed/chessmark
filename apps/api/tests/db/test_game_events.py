"""The event log must be gap-free under concurrency (ADR-0008).

`game_events.seq` is load-bearing for three separate features: live streaming, SSE reconnect via
`Last-Event-ID`, and the replay scrubber. A gap means a reconnecting client waits forever for an
event that never existed; a duplicate means it sees a ply twice. Neither is acceptable, and both
are exactly the kind of bug that only appears when two workers write at once.
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chessmark.db.enums import EventType
from chessmark.db.models import GameEvent
from chessmark.db.repositories import (
    GameNotFoundError,
    append_event,
    create_game,
    load_events,
)
from chessmark.game import ChessBoard

STARTING_FEN = ChessBoard().fen
CONCURRENT_APPENDS = 100


@pytest.mark.integration
async def test_sequence_starts_at_one_and_increments(db: AsyncSession) -> None:
    game = await create_game(db, start_fen=STARTING_FEN)

    first = await append_event(db, game_id=game.id, type=EventType.GAME_STARTED)
    second = await append_event(db, game_id=game.id, type=EventType.TURN_STARTED)

    assert first.seq == 1
    assert second.seq == 2


@pytest.mark.integration
async def test_sequences_are_independent_per_game(db: AsyncSession) -> None:
    one = await create_game(db, start_fen=STARTING_FEN)
    two = await create_game(db, start_fen=STARTING_FEN)

    await append_event(db, game_id=one.id, type=EventType.GAME_STARTED)
    await append_event(db, game_id=one.id, type=EventType.TURN_STARTED)
    other = await append_event(db, game_id=two.id, type=EventType.GAME_STARTED)

    assert other.seq == 1, "a new game starts its own sequence"


@pytest.mark.integration
async def test_payload_round_trips_as_jsonb(db: AsyncSession) -> None:
    game = await create_game(db, start_fen=STARTING_FEN)
    payload = {
        "san": "Nf3",
        "ply": 27,
        "nested": {"tokens": 340, "cached": True},
        "list": [1, 2, 3],
    }

    await append_event(db, game_id=game.id, type=EventType.MOVE_MADE, payload=payload)
    await db.commit()
    db.expunge_all()

    events = await load_events(db, game.id)
    assert events[0].payload == payload
    assert events[0].type is EventType.MOVE_MADE


@pytest.mark.integration
async def test_appending_to_a_missing_game_raises(db: AsyncSession) -> None:
    import uuid

    with pytest.raises(GameNotFoundError):
        await append_event(db, game_id=uuid.uuid4(), type=EventType.GAME_STARTED)


# ------------------------------------------------------------------ the real test


@pytest.mark.integration
async def test_concurrent_appends_produce_a_gap_free_sequence(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """100 appends racing on one game must yield exactly seq 1..100.

    Each append runs in its own session and its own transaction, so they genuinely contend. The
    `UPDATE ... RETURNING` on the parent row is what serialises them.
    """
    game = await create_game(db, start_fen=STARTING_FEN)
    await db.commit()

    async def append_one(index: int) -> int:
        async with sessionmaker() as session, session.begin():
            event = await append_event(
                session,
                game_id=game.id,
                type=EventType.THINKING,
                payload={"worker": index},
            )
            return event.seq

    sequences = await asyncio.gather(*(append_one(i) for i in range(CONCURRENT_APPENDS)))

    assert sorted(sequences) == list(range(1, CONCURRENT_APPENDS + 1)), (
        "concurrent appends produced gaps or duplicates"
    )
    assert len(set(sequences)) == CONCURRENT_APPENDS


@pytest.mark.integration
async def test_concurrent_appends_are_all_persisted(
    db: AsyncSession, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    game = await create_game(db, start_fen=STARTING_FEN)
    await db.commit()

    async def append_one(index: int) -> None:
        async with sessionmaker() as session, session.begin():
            await append_event(
                session, game_id=game.id, type=EventType.THINKING, payload={"worker": index}
            )

    await asyncio.gather(*(append_one(i) for i in range(CONCURRENT_APPENDS)))

    stored = await db.scalars(
        sa.select(GameEvent.seq).where(GameEvent.game_id == game.id).order_by(GameEvent.seq)
    )
    assert list(stored) == list(range(1, CONCURRENT_APPENDS + 1))

    workers = await db.scalars(
        sa.select(GameEvent.payload["worker"].astext.cast(sa.Integer)).where(
            GameEvent.game_id == game.id
        )
    )
    assert sorted(workers) == list(range(CONCURRENT_APPENDS)), "every append survived"


@pytest.mark.integration
async def test_duplicate_sequence_is_rejected_by_the_constraint(db: AsyncSession) -> None:
    """The counter is the mechanism; the unique constraint is the backstop."""
    game = await create_game(db, start_fen=STARTING_FEN)
    await append_event(db, game_id=game.id, type=EventType.GAME_STARTED)
    await db.commit()

    db.add(GameEvent(game_id=game.id, seq=1, type=EventType.TURN_STARTED, payload={}))

    with pytest.raises(Exception, match=r"uq_game_events_game_id_seq|duplicate key"):
        await db.commit()


# ------------------------------------------------------------------ reconnect


@pytest.mark.integration
async def test_cursor_replays_exactly_what_was_missed(db: AsyncSession) -> None:
    """UI-10: a client reconnecting with Last-Event-ID gets the gap and nothing else."""
    game = await create_game(db, start_fen=STARTING_FEN)
    for index in range(10):
        await append_event(
            db, game_id=game.id, type=EventType.MOVE_MADE, payload={"ply": index + 1}
        )
    await db.commit()

    missed = await load_events(db, game.id, after_seq=6)

    assert [event.seq for event in missed] == [7, 8, 9, 10]
    assert [event.payload["ply"] for event in missed] == [7, 8, 9, 10]


@pytest.mark.integration
async def test_cursor_at_the_head_returns_nothing(db: AsyncSession) -> None:
    game = await create_game(db, start_fen=STARTING_FEN)
    await append_event(db, game_id=game.id, type=EventType.GAME_STARTED)
    await db.commit()

    assert await load_events(db, game.id, after_seq=1) == []


@pytest.mark.integration
async def test_events_can_be_paged(db: AsyncSession) -> None:
    game = await create_game(db, start_fen=STARTING_FEN)
    for _ in range(10):
        await append_event(db, game_id=game.id, type=EventType.THINKING)
    await db.commit()

    page = await load_events(db, game.id, after_seq=0, limit=3)
    assert [event.seq for event in page] == [1, 2, 3]

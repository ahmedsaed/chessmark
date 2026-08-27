"""The turn worker: idempotency, crash resumption, budgets, and a whole game.

These are the properties ADR-0007 exists to provide. They only hold as an interaction between the
queue, the database, and the worker, so all three are real here — only the provider is scripted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import plays, scripted, step, tool_call
from chessmark.agents.turn import TurnLimits
from chessmark.db.enums import EventType, GameStatus, TurnStatus
from chessmark.db.models import Game, GameEvent, LlmCall, Player, Ply, ToolCall, Turn
from chessmark.game import GameResult, Termination
from chessmark.orchestration.queue import AdvanceTurn
from chessmark.orchestration.worker import (
    ABORTED,
    ADVANCED,
    BUDGET,
    GAME_OVER,
    MAX_JOB_ATTEMPTS,
    NOT_RUNNING,
    STALE,
    TURN_FAILED,
)
from tests.orchestration.conftest import Fixture, both_sides, run_next, seat_match

pytestmark = pytest.mark.integration


# ====================================================================== one turn


async def test_a_job_advances_the_game_by_one_ply(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    worker = make_worker(plays(["e4"]))

    handled = await worker.handle(game.first_job)

    assert handled.outcome == ADVANCED
    assert handled.ply == 1

    db.expunge_all()
    plies = (await db.scalars(sa.select(Ply).where(Ply.game_id == game.game.id))).all()
    assert [p.san for p in plies] == ["e4"]


async def test_advancing_enqueues_the_next_turn(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """The chain that keeps a game moving with no scheduler."""
    await run_next(make_worker(plays(["e4"])), game.queue)

    deliveries = await game.queue.consume("checker", block_ms=100)

    assert len(deliveries) == 1
    assert deliveries[0].job.expected_ply == 1, "the next job must expect the ply just played"
    assert deliveries[0].job.game_id == game.game.id


async def test_a_finished_game_enqueues_nothing(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    worker = make_worker(scripted(step(tool_call("resign"))))

    handled = await run_next(worker, game.queue)

    assert handled.outcome == GAME_OVER
    assert await game.queue.consume("checker", block_ms=100) == []


# ====================================================================== idempotency


async def test_a_replayed_job_is_a_no_op(db: AsyncSession, game: Fixture, make_worker) -> None:
    """Exit criterion, and the reason at-least-once delivery is safe (ADR-0007)."""
    worker = make_worker(plays(["e4", "d4"]))

    first = await worker.handle(game.first_job)
    assert first.outcome == ADVANCED

    replayed = await worker.handle(game.first_job)

    assert replayed.outcome == STALE
    assert replayed.ply == 1

    db.expunge_all()
    plies = (await db.scalars(sa.select(Ply).where(Ply.game_id == game.game.id))).all()
    assert len(plies) == 1, "the replay must not have played a second move"


async def test_a_stale_job_writes_nothing_at_all(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """A no-op must be a genuine no-op — no turn row, no LLM call, no wasted spend."""
    worker = make_worker(plays(["e4"]))
    await worker.handle(game.first_job)

    db.expunge_all()
    before = {
        "turns": await db.scalar(sa.select(sa.func.count()).select_from(Turn)),
        "llm_calls": await db.scalar(sa.select(sa.func.count()).select_from(LlmCall)),
        "events": await db.scalar(sa.select(sa.func.count()).select_from(GameEvent)),
    }

    await worker.handle(game.first_job)

    db.expunge_all()
    after = {
        "turns": await db.scalar(sa.select(sa.func.count()).select_from(Turn)),
        "llm_calls": await db.scalar(sa.select(sa.func.count()).select_from(LlmCall)),
        "events": await db.scalar(sa.select(sa.func.count()).select_from(GameEvent)),
    }
    assert before == after


async def test_a_job_from_the_future_is_also_dropped(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """Idempotency is an equality check, not a `<`. A job ahead of reality is equally wrong."""
    handled = await make_worker(plays(["e4"])).handle(
        AdvanceTurn(game_id=game.game.id, expected_ply=7)
    )

    assert handled.outcome == STALE


async def test_a_job_for_a_pending_game_is_dropped(db: AsyncSession, queue, make_worker) -> None:
    """A game that was never started must not be played by a stray job."""
    from chessmark.orchestration.match import Seat, create_match

    match = await create_match(db, white=Seat(display_name="w"), black=Seat(display_name="b"))
    await db.commit()

    handled = await make_worker(plays(["e4"])).handle(
        AdvanceTurn(game_id=match.game.id, expected_ply=0)
    )

    assert handled.outcome == NOT_RUNNING


# ====================================================================== crash resumption


async def test_a_crash_mid_turn_leaves_no_trace(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """OPS-05. One transaction per turn, so a crash rolls back rather than half-writing."""

    async def killed(**_kwargs: object) -> object:
        # KeyboardInterrupt, not Exception: the gateway deliberately converts every Exception into
        # an LlmError, so an ordinary exception here would test the provider-failure path instead
        # of a crash. A BaseException is what an actual kill signal looks like.
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await make_worker(killed).handle(game.first_job)

    db.expunge_all()
    assert await db.scalar(sa.select(sa.func.count()).select_from(Ply)) == 0
    assert await db.scalar(sa.select(sa.func.count()).select_from(Turn)) == 0

    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.ply_count == 0
    assert reloaded.status is GameStatus.RUNNING, "the game must still be playable"


async def test_the_job_survives_a_crash_and_the_turn_reruns(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """OPS-05, end to end: the delivery is unacked, reclaimed, and replayed successfully."""

    async def killed(**_kwargs: object) -> object:
        raise KeyboardInterrupt

    delivered = await game.queue.consume("doomed-worker", block_ms=500)
    assert len(delivered) == 1

    with pytest.raises(KeyboardInterrupt):
        await make_worker(killed, consumer="doomed-worker").handle(delivered[0].job)

    # The worker died before acking, so the job is still pending against it.
    assert await game.queue.pending_count() == 1

    reclaimed = await game.queue.reclaim_stalled("rescuer", min_idle_ms=0)
    assert len(reclaimed) == 1
    assert reclaimed[0].redelivered
    assert reclaimed[0].job.expected_ply == 0, "the rerun starts from the same ply"

    handled = await make_worker(plays(["e4"]), consumer="rescuer").process(reclaimed[0])

    assert handled.outcome == ADVANCED
    assert await game.queue.pending_count() == 0, "the rescued job was acked"

    db.expunge_all()
    plies = (await db.scalars(sa.select(Ply))).all()
    assert [p.san for p in plies] == ["e4"]


async def test_a_job_is_acked_even_when_the_turn_is_a_no_op(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """An unacked stale job would be redelivered forever, blocking everything behind it."""
    worker = make_worker(plays(["e4"]))
    delivered = await game.queue.consume("w", block_ms=500)
    await worker.process(delivered[0])

    replay = await game.queue.consume("w", block_ms=200)
    for delivery in replay:
        await worker.process(delivery)

    assert await game.queue.pending_count() == 0


# ====================================================================== budgets


async def test_a_game_over_budget_stops_before_spending_more(
    db: AsyncSession, queue, make_worker
) -> None:
    """Exit criterion. ADR-0011 layer 3, checked *before* a turn — noticing afterwards means the
    money is already gone."""
    fixture = await seat_match(db, queue, max_usd=Decimal("0.01"))

    # Simulate a game that has already spent its allowance.
    await db.execute(
        sa.update(Game).where(Game.id == fixture.game.id).values(total_cost_usd=Decimal("0.02"))
    )
    await db.commit()

    handled = await make_worker(plays(["e4"])).handle(fixture.first_job)

    assert handled.outcome == BUDGET
    assert handled.game_outcome is not None
    assert handled.game_outcome.termination is Termination.BUDGET_EXCEEDED
    assert handled.game_outcome.result is GameResult.DRAW, (
        "a budget stop is our decision, not a chess result — neither model may be awarded the win"
    )

    db.expunge_all()
    reloaded = await db.get(Game, fixture.game.id)
    assert reloaded is not None
    assert reloaded.status is GameStatus.FINISHED
    assert await db.scalar(sa.select(sa.func.count()).select_from(LlmCall)) == 0, (
        "the budget stop must happen before any provider call"
    )


async def test_a_game_under_budget_proceeds(db: AsyncSession, queue, make_worker) -> None:
    fixture = await seat_match(db, queue, max_usd=Decimal("1.00"))

    handled = await make_worker(plays(["e4"])).handle(fixture.first_job)

    assert handled.outcome == ADVANCED


async def test_the_ply_cap_ends_the_game(db: AsyncSession, queue, make_worker) -> None:
    """GAME-07. With no engine configured the cap adjudicates a draw."""
    fixture = await seat_match(db, queue, max_plies=2)
    worker = make_worker(both_sides(["e4"], ["e5"]))

    job = fixture.first_job
    for _ in range(2):
        handled = await worker.handle(job)
        job = AdvanceTurn(game_id=fixture.game.id, expected_ply=handled.ply)

    assert handled.game_outcome is not None
    assert handled.game_outcome.termination is Termination.PLY_CAP
    assert handled.game_outcome.result is GameResult.DRAW


# ====================================================================== provider failures
#
# **An outage, not a rate limit.** These used to raise a 429 as a convenient stand-in for "the
# provider is having a bad day", and a 429 no longer travels this path at all: it pauses the game
# instead of spending its retry budget (see `test_pause_on_rate_limit.py`). A 503 is what this
# branch is actually for — a provider that is broken rather than one that has told us to come back.


class OutageError(Exception):
    """Shaped like the provider library's error, which carries its status on the exception."""

    status_code = 503


async def unavailable(**_kwargs: object) -> object:
    raise OutageError


async def test_a_provider_failure_requeues_the_same_ply(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """AGENT-09: our outage is not the model's failure, so the game is untouched and retried."""

    handled = await run_next(make_worker(unavailable), game.queue)

    assert handled.outcome == TURN_FAILED

    requeued = await game.queue.consume("checker", block_ms=200)
    assert len(requeued) == 1
    assert requeued[0].job.expected_ply == 0, "the same ply is retried"
    assert requeued[0].job.attempt == 2


async def test_a_provider_failure_leaves_the_transcript_untouched(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """The reason the turn rolls back: a committed failed turn would append a second turn prompt,
    so the model would see 'It is your move' twice with a dead exchange between them."""
    from chessmark.agents import transcript

    before = await transcript.build_messages(db, game.white.id)

    await make_worker(unavailable).handle(game.first_job)

    db.expunge_all()
    after = await transcript.build_messages(db, game.white.id)

    assert after == before
    assert len(after) == 1, "only the system prompt should exist"


async def test_repeated_provider_failure_abandons_rather_than_forfeits(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """Nobody played badly — our provider was unavailable. An abandoned game is excluded from
    ratings, not counted as a loss."""

    handled = await make_worker(unavailable).handle(
        AdvanceTurn(game_id=game.game.id, expected_ply=0, attempt=MAX_JOB_ATTEMPTS)
    )

    assert handled.outcome == ABORTED

    db.expunge_all()
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.status is GameStatus.ABORTED
    assert reloaded.termination is Termination.ABANDONED
    assert reloaded.winner_colour is None, "no player may be blamed for our outage"

    players = (await db.scalars(sa.select(Player).where(Player.game_id == game.game.id))).all()
    assert players
    assert not any(player.forfeited for player in players)


# ====================================================================== a whole game


async def test_two_scripted_models_play_a_full_game_unattended(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """Exit criterion: a complete game to a legitimate terminal state, driven only by the queue."""
    worker = make_worker(both_sides(["f3", "g4"], ["e5", "Qh4"]))

    played = 0
    while played < 20:
        deliveries = await game.queue.consume("solo", block_ms=200)
        if not deliveries:
            break
        for delivery in deliveries:
            await worker.process(delivery)
            played += 1

    db.expunge_all()
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None

    assert reloaded.status is GameStatus.FINISHED
    assert reloaded.result is GameResult.BLACK_WINS
    assert reloaded.termination is Termination.CHECKMATE
    assert reloaded.ply_count == 4

    plies = (await db.scalars(sa.select(Ply).order_by(Ply.ply_number))).all()
    assert [p.san for p in plies] == ["f3", "e5", "g4", "Qh4#"]

    assert await game.queue.pending_count() == 0, "no job left in flight"


async def test_a_full_game_records_everything(db: AsyncSession, game: Fixture, make_worker) -> None:
    """Exit criterion: Postgres holds every ply, turn, LLM call, tool call, and event."""
    worker = make_worker(both_sides(["f3", "g4"], ["e5", "Qh4"]))

    for _ in range(10):
        deliveries = await game.queue.consume("solo", block_ms=200)
        if not deliveries:
            break
        for delivery in deliveries:
            await worker.process(delivery)

    db.expunge_all()
    counts = {
        "plies": await db.scalar(sa.select(sa.func.count()).select_from(Ply)),
        "turns": await db.scalar(sa.select(sa.func.count()).select_from(Turn)),
        "llm_calls": await db.scalar(sa.select(sa.func.count()).select_from(LlmCall)),
        "tool_calls": await db.scalar(sa.select(sa.func.count()).select_from(ToolCall)),
        "events": await db.scalar(sa.select(sa.func.count()).select_from(GameEvent)),
    }

    assert counts["plies"] == 4
    assert counts["turns"] == 4
    assert counts["llm_calls"] >= 4
    assert counts["tool_calls"] >= 4
    assert counts["events"] > 4

    calls = (await db.scalars(sa.select(LlmCall))).all()
    assert all(call.request for call in calls), "requests must be stored verbatim"

    turns = (await db.scalars(sa.select(Turn))).all()
    assert all(turn.status is TurnStatus.COMPLETED for turn in turns)


async def test_the_event_log_is_gap_free_across_a_whole_game(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    """ADR-0008: SSE reconnect depends on this holding over the full life of a game."""
    worker = make_worker(both_sides(["f3", "g4"], ["e5", "Qh4"]))

    for _ in range(10):
        deliveries = await game.queue.consume("solo", block_ms=200)
        if not deliveries:
            break
        for delivery in deliveries:
            await worker.process(delivery)

    db.expunge_all()
    seqs = (
        await db.scalars(
            sa.select(GameEvent.seq)
            .where(GameEvent.game_id == game.game.id)
            .order_by(GameEvent.seq)
        )
    ).all()

    assert list(seqs) == list(range(1, len(seqs) + 1))


async def test_events_are_published_after_commit(
    db: AsyncSession, game: Fixture, make_worker, redis
) -> None:
    """Published only after the transaction commits — a subscriber must never see a turn that
    later rolled back."""
    channel = f"chessmark:game:{game.game.id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    worker = make_worker(plays(["e4"]), publish=True)
    await worker.handle(game.first_job)

    received = []
    for _ in range(50):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message:
            received.append(message)

    await pubsub.unsubscribe(channel)
    await pubsub.aclose()

    assert received, "no events reached the channel"


async def test_a_turn_limit_forfeit_ends_the_game_through_the_worker(
    db: AsyncSession, game: Fixture, make_worker
) -> None:
    worker = make_worker(
        scripted(step(tool_call("get_board")), repeat_last=True),
        limits=TurnLimits(max_tool_iterations=3),
    )

    handled = await worker.handle(game.first_job)

    assert handled.game_outcome is not None
    assert handled.game_outcome.termination is Termination.ERROR_FORFEIT

    db.expunge_all()
    reloaded = await db.get(Game, game.game.id)
    assert reloaded is not None
    assert reloaded.status is GameStatus.FINISHED


async def test_a_game_ends_exactly_once(db: AsyncSession, game: Fixture, make_worker) -> None:
    """The turn runner and the worker both knew the game was over and both announced it, so every
    game emitted two `game_ended` events — rendered twice live, and twice again in replay.
    Announcing belongs to whoever owns the status transition, which is the worker."""
    worker = make_worker(both_sides(["f3", "g4"], ["e5", "Qh4"]))

    for _ in range(10):
        deliveries = await game.queue.consume("solo", block_ms=200)
        if not deliveries:
            break
        for delivery in deliveries:
            await worker.process(delivery)

    db.expunge_all()
    endings = (
        await db.scalars(
            sa.select(GameEvent).where(
                GameEvent.game_id == game.game.id, GameEvent.type == EventType.GAME_ENDED
            )
        )
    ).all()

    assert len(endings) == 1, f"expected one game_ended event, got {len(endings)}"
    assert endings[0].payload["termination"] == "checkmate"

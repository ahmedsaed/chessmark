"""A person playing a model (Phase 10).

The through-line of every test here is invariant 1: the server is the only authority on the board.
A human proposes exactly the way a model does, and `Referee` disposes — so these assert what the
*server* did, never what a client was allowed to try.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from chessmark.db.enums import EventType, GameStatus, ModerationStatus
from chessmark.db.models import GameEvent, Message, Ply, TranscriptMessage
from chessmark.game import Colour, GameResult, IllegalMoveError, Termination
from chessmark.orchestration import human as human_play
from chessmark.orchestration.reconciler import DEFAULT_HUMAN_IDLE_AFTER, reconcile
from chessmark.orchestration.worker import AWAITING_HUMAN
from tests.support import both_sides, make_user, run_next, seat_human_match, seat_match

pytestmark = pytest.mark.integration


async def _events(db, game_id, type_):
    return list(
        await db.scalars(
            sa.select(GameEvent).where(GameEvent.game_id == game_id, GameEvent.type == type_)
        )
    )


# ---------------------------------------------------------------- the worker stops


async def test_worker_stops_at_a_human_turn_and_enqueues_nothing(db, queue, make_worker):
    """The central seam.

    Without this the worker would find a human to move and run an LLM turn on their behalf —
    playing the person's move for them, and charging them for it.
    """
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user, human_colour=Colour.WHITE)
    worker = make_worker(both_sides([], []))

    handled = await run_next(worker, queue)

    assert handled.outcome == AWAITING_HUMAN
    assert handled.ply == 0
    # Nothing requeued: a job that requeued itself would spin for as long as the person thought.
    # `depth()` is XLEN and counts acked entries, so ask what a worker would actually pick up.
    assert await queue.consume("assert-empty", block_ms=100) == []
    # And nothing was spent.
    await db.refresh(fixture.game)
    assert fixture.game.ply_count == 0
    assert fixture.game.total_cost_usd == 0


async def test_model_moves_first_when_the_human_is_black(db, queue, make_worker):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user, human_colour=Colour.BLACK)
    worker = make_worker(both_sides(["e4"], []))

    handled = await run_next(worker, queue)

    assert handled.outcome != AWAITING_HUMAN
    await db.refresh(fixture.game)
    assert fixture.game.ply_count == 1


# ---------------------------------------------------------------- moving


async def test_a_human_move_commits_one_ply_and_one_event(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    action = await human_play.play_move(db, game=fixture.game, player=fixture.white, move_text="e4")
    await db.commit()

    assert action.ply == 1
    assert action.outcome is None

    plies = list(await db.scalars(sa.select(Ply).where(Ply.game_id == fixture.game.id)))
    assert [p.san for p in plies] == ["e4"]
    # A human move has no turn: there was no LLM call, no tokens and no cost to hang off one.
    assert plies[0].turn_id is None

    # Exactly one event per state change (invariant 7).
    moved = await _events(db, fixture.game.id, EventType.MOVE_MADE)
    assert len(moved) == 1
    assert moved[0].payload["san"] == "e4"
    assert moved[0].payload["human"] is True


async def test_uci_is_accepted_as_well_as_algebraic(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    action = await human_play.play_move(
        db, game=fixture.game, player=fixture.white, move_text="e2e4"
    )
    assert action.detail == "e4"


async def test_an_illegal_move_is_refused_and_records_nothing(db, queue):
    """A crafted request that skips the client meets the same referee (invariant 1)."""
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    with pytest.raises(IllegalMoveError) as raised:
        await human_play.play_move(db, game=fixture.game, player=fixture.white, move_text="e5")

    # The full legal move list travels with the refusal, as it does for a model (ADR-0002).
    assert "e4" in raised.value.legal_moves_san

    await db.rollback()
    await db.refresh(fixture.game)
    assert fixture.game.ply_count == 0
    assert await _events(db, fixture.game.id, EventType.MOVE_MADE) == []
    # And crucially: no forfeit. It is still their turn.
    assert fixture.game.status is GameStatus.RUNNING


async def test_moving_out_of_turn_is_refused(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user, human_colour=Colour.BLACK)

    with pytest.raises(human_play.NotYourTurnError):
        await human_play.play_move(db, game=fixture.game, player=fixture.black, move_text="e5")


async def test_a_resubmitted_move_is_refused_rather_than_played_twice(db, queue):
    """The client's `expected_ply` is the same guarantee `AdvanceTurn` gives the queue."""
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    await human_play.play_move(
        db, game=fixture.game, player=fixture.white, move_text="e4", expected_ply=0
    )
    await db.commit()

    with pytest.raises(human_play.StalePlyError):
        await human_play.play_move(
            db, game=fixture.game, player=fixture.white, move_text="d4", expected_ply=0
        )


async def test_only_the_seat_holder_may_act(db, queue):
    user = await make_user(db, "user_seated")
    stranger = await make_user(db, "user_stranger")
    fixture = await seat_human_match(db, queue, user=user)
    await db.commit()

    assert (await human_play.seat_of(db, fixture.game.id, user.id)).id == fixture.white.id

    with pytest.raises(human_play.NotYourGameError):
        await human_play.seat_of(db, fixture.game.id, stranger.id)


async def test_a_model_only_game_has_no_human_seat(db, queue):
    user = await make_user(db)
    fixture = await seat_match(db, queue)

    with pytest.raises(human_play.NotYourGameError):
        await human_play.seat_of(db, fixture.game.id, user.id)


# ---------------------------------------------------------------- resigning and draws


async def test_resigning_ends_the_game_for_the_resigning_colour(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user, human_colour=Colour.WHITE)

    action = await human_play.resign(db, game=fixture.game, player=fixture.white)
    await db.commit()

    assert action.game_over
    assert action.outcome.result is GameResult.BLACK_WINS
    assert action.outcome.termination is Termination.RESIGNATION

    await db.refresh(fixture.game)
    assert fixture.game.status is GameStatus.FINISHED
    assert len(await _events(db, fixture.game.id, EventType.GAME_ENDED)) == 1


async def test_accepting_the_models_draw_offer_ends_the_game(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user, human_colour=Colour.WHITE)

    # Stand in for the model's `offer_draw` tool: the same event, at the same ply.
    from chessmark.db.repositories import append_event

    await append_event(
        db,
        game_id=fixture.game.id,
        type=EventType.DRAW_OFFERED,
        payload={"player_id": str(fixture.black.id), "colour": "black", "ply": 0},
    )

    action = await human_play.respond_to_draw(
        db, game=fixture.game, player=fixture.white, accept=True
    )
    await db.commit()

    assert action.game_over
    assert action.outcome.result is GameResult.DRAW
    assert action.outcome.termination is Termination.AGREED_DRAW


async def test_declining_a_draw_leaves_the_game_running(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    from chessmark.db.repositories import append_event

    await append_event(
        db,
        game_id=fixture.game.id,
        type=EventType.DRAW_OFFERED,
        payload={"player_id": str(fixture.black.id), "colour": "black", "ply": 0},
    )

    action = await human_play.respond_to_draw(
        db, game=fixture.game, player=fixture.white, accept=False
    )
    assert not action.game_over
    await db.refresh(fixture.game)
    assert fixture.game.status is GameStatus.RUNNING


async def test_a_draw_offer_lapses_once_a_move_is_played(db, queue):
    """As over a board: an offer stands only for the position it was made in."""
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    from chessmark.db.repositories import append_event

    await append_event(
        db,
        game_id=fixture.game.id,
        type=EventType.DRAW_OFFERED,
        payload={"player_id": str(fixture.black.id), "colour": "black", "ply": 0},
    )
    await human_play.play_move(db, game=fixture.game, player=fixture.white, move_text="e4")
    await db.commit()

    with pytest.raises(human_play.NotYourTurnError, match="no draw offer"):
        await human_play.respond_to_draw(db, game=fixture.game, player=fixture.white, accept=True)


async def test_you_cannot_accept_your_own_draw_offer(db, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    await human_play.offer_draw(db, game=fixture.game, player=fixture.white)

    with pytest.raises(human_play.NotYourTurnError, match="your own"):
        await human_play.respond_to_draw(db, game=fixture.game, player=fixture.white, accept=True)


# ---------------------------------------------------------------- talking


async def test_a_human_message_reaches_the_models_transcript(db, queue):
    """Delivered through the same line a model's own `say` uses (TALK-06).

    A model must not be able to tell a person from another model by the shape of the prompt.
    """
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    await human_play.say(db, game=fixture.game, player=fixture.white, message="good luck")
    await db.commit()

    rows = list(
        await db.scalars(
            sa.select(TranscriptMessage)
            .where(TranscriptMessage.player_id == fixture.black.id)
            .order_by(TranscriptMessage.seq)
        )
    )
    assert rows[-1].role == "user"
    assert rows[-1].content == "Your opponent says: good luck"

    stored = list(await db.scalars(sa.select(Message).where(Message.game_id == fixture.game.id)))
    assert len(stored) == 1
    # Unmoderated until Phase 11, and stored as such rather than silently approved.
    assert stored[0].moderation_status is ModerationStatus.PENDING


@pytest.mark.parametrize("message", ["", "   ", "x" * 501])
async def test_empty_and_oversized_messages_are_refused(db, queue, message):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)

    with pytest.raises(ValueError):
        await human_play.say(db, game=fixture.game, player=fixture.white, message=message)


# ---------------------------------------------------------------- abandonment


async def test_a_game_waiting_on_a_person_is_neither_requeued_nor_abandoned(
    db, sessionmaker, queue
):
    """Waiting is not stalling.

    Without this distinction a human game would be requeued every cycle forever, and would never
    be written off.
    """
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)
    await db.commit()

    report = await reconcile(sessionmaker, queue, stale_after=dt.timedelta(0))

    assert str(fixture.game.id) in report.waiting
    assert report.requeued == []
    assert report.abandoned == []


async def test_an_idle_human_game_is_abandoned(db, sessionmaker, queue):
    user = await make_user(db)
    fixture = await seat_human_match(db, queue, user=user)
    await db.commit()

    report = await reconcile(
        sessionmaker, queue, stale_after=dt.timedelta(0), human_idle_after=dt.timedelta(0)
    )

    assert str(fixture.game.id) in report.abandoned
    await db.refresh(fixture.game)
    assert fixture.game.status is GameStatus.ABORTED
    assert fixture.game.termination is Termination.ABANDONED
    # Never a result: nobody won, and the model did nothing to earn a win.
    assert fixture.game.result is GameResult.ONGOING


async def test_a_stalled_model_game_is_still_requeued(db, sessionmaker, queue):
    """The abandonment path must not swallow the case the reconciler exists for."""
    fixture = await seat_match(db, queue)
    await db.commit()

    report = await reconcile(
        sessionmaker, queue, stale_after=dt.timedelta(0), human_idle_after=dt.timedelta(0)
    )

    assert str(fixture.game.id) in report.requeued
    assert report.abandoned == []


async def test_the_idle_window_is_far_longer_than_the_stall_window(db):
    """A person may leave the tab open; a stalled model game is our fault and is urgent.

    The multiple used to be four, when the stall window was twenty minutes. It is 45 now — a
    healthy slow turn can take longer than twenty minutes since the per-call timeout became ten
    (ADR-0017), and the sweep was manufacturing duplicate jobs for turns that were fine (ADR-0022).
    The property being protected is the *ordering*, comfortably: a person waits far longer than a
    stalled model does. Four was never the point, and pinning it here would make the stall window
    un-tunable without touching a human-facing number that has nothing to do with it.
    """
    from chessmark.orchestration.reconciler import DEFAULT_STALE_AFTER

    assert DEFAULT_HUMAN_IDLE_AFTER > DEFAULT_STALE_AFTER * 2

"""A draw by agreement between two models (ADR-0040).

`offer_draw` has existed since the first tool schema and, in a model-vs-model game, did nothing at
all: it returned *"Draw offered. Your opponent will respond on its turn"* to the seat that called
it, wrote no event, and told the opponent nothing. The only code that ever recorded an offer was
the human path. There was also no `accept_draw`, so even a delivered offer had no answer — which
`DRAW_OFFER_RECEIVED` said out loud: *"There is no tool to accept it, so play on."*

`Termination.AGREED_DRAW` was therefore unreachable in every ranked game ever played.

The offer travels in the **turn prompt** rather than a message of its own, because that is already
the one message saying what has happened since this seat last acted.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.enums import EventType, TurnStatus
from chessmark.db.models import GameEvent, TranscriptMessage
from chessmark.db.repositories import open_draw_offer
from chessmark.game import Colour, GameResult, Termination
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration


async def _turn_prompts(db: AsyncSession, player_id: object) -> list[str]:
    rows = await db.scalars(
        sa.select(TranscriptMessage.content)
        .where(TranscriptMessage.player_id == player_id, TranscriptMessage.role == "user")
        .order_by(TranscriptMessage.seq)
    )
    return [row or "" for row in rows]


async def _offers(db: AsyncSession, game_id: object) -> list[GameEvent]:
    return list(
        await db.scalars(
            sa.select(GameEvent)
            .where(GameEvent.game_id == game_id, GameEvent.type == EventType.DRAW_OFFERED)
            .order_by(GameEvent.seq)
        )
    )


async def _offer_then_move(db: AsyncSession, table: Table) -> None:
    """White offers a draw and moves, which is the only shape a model can offer in."""
    await play_turn(
        db,
        table,
        scripted(
            step(tool_call("offer_draw")),
            step(tool_call("make_move", move="e4")),
        ),
        colour=Colour.WHITE,
    )


# ====================================================================== the offer travels


async def test_a_models_offer_is_recorded(db: AsyncSession, table: Table) -> None:
    """One `game_events` row, as every state change gets (invariant 7). There were none."""
    await _offer_then_move(db, table)

    offers = await _offers(db, table.game.id)
    assert len(offers) == 1
    assert offers[0].payload["player_id"] == str(table.white.id)
    assert offers[0].payload["colour"] == "white"


async def test_the_opponent_is_told_in_its_turn_prompt(db: AsyncSession, table: Table) -> None:
    """The half that was missing entirely: the opponent never heard about it."""
    await _offer_then_move(db, table)
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )

    prompt = (await _turn_prompts(db, table.black.id))[-1]
    assert "offered a draw" in prompt, prompt
    assert "accept_draw" in prompt, "and it must name the tool that answers it"
    assert "White played e4" in prompt, "without losing the move it arrived with"


async def test_the_offerer_is_not_told_about_its_own_offer(db: AsyncSession, table: Table) -> None:
    """`accept_draw` would otherwise let a seat draw a game by agreeing with itself."""
    await _offer_then_move(db, table)
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="d4"))), colour=Colour.WHITE
    )

    assert "offered a draw" not in (await _turn_prompts(db, table.white.id))[-1]


# ====================================================================== answering it


async def test_accepting_ends_the_game_as_a_draw(db: AsyncSession, table: Table) -> None:
    """`Termination.AGREED_DRAW` was unreachable between two models before this."""
    await _offer_then_move(db, table)

    result = await play_turn(
        db, table, scripted(step(tool_call("accept_draw"))), colour=Colour.BLACK
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.outcome is not None
    assert result.outcome.result is GameResult.DRAW
    assert result.outcome.termination is Termination.AGREED_DRAW


async def test_moving_instead_declines_it(db: AsyncSession, table: Table) -> None:
    """As over a board: the move is the decline, and nothing has to be said."""
    await _offer_then_move(db, table)
    result = await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )

    assert result.moved
    assert not table.referee.is_over
    assert await open_draw_offer(db, game=table.game) is None, "answered, so it no longer stands"


async def test_accepting_nothing_is_refused_and_is_not_an_illegal_move(
    db: AsyncSession, table: Table
) -> None:
    """The `claim_draw` rule, for the same reason (ADR-0020): a model asking whether an offer is
    open has broken no rule, and charging it against the retry budget would forfeit a seat for
    asking."""
    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("accept_draw")),
            step(tool_call("make_move", move="e4")),
        ),
        colour=Colour.WHITE,
    )

    assert result.moved, "the turn carries on"
    assert not table.referee.is_over
    assert result.illegal_attempts == 0, (
        "a refused acceptance is a question answered, not a rule broken"
    )


# ====================================================================== when it lapses


async def test_the_offerers_own_move_does_not_lapse_its_offer(
    db: AsyncSession, table: Table
) -> None:
    """**The whole of the fix.**

    A model must still move in the turn it offers in, so a rule keyed on the *position* — which is
    what `open_draw_offer` used before, `payload["ply"] != referee.ply` — cancelled every model
    offer one ply before the opponent could ever see it. Right for a human, who offers as a
    separate action and does not move afterwards; wrong for a model.
    """
    await _offer_then_move(db, table)

    assert await open_draw_offer(db, game=table.game) == table.white.id


async def test_the_recipients_move_lapses_it(db: AsyncSession, table: Table) -> None:
    await _offer_then_move(db, table)
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )

    assert await open_draw_offer(db, game=table.game) is None


async def test_an_offer_cannot_be_accepted_two_turns_later(db: AsyncSession, table: Table) -> None:
    """It lapsed when Black moved, so Black cannot come back to it on its next turn."""
    await _offer_then_move(db, table)
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="d4"))), colour=Colour.WHITE
    )

    result = await play_turn(
        db,
        table,
        scripted(
            step(tool_call("accept_draw")),
            step(tool_call("make_move", move="d5")),
        ),
        colour=Colour.BLACK,
    )

    assert result.moved
    assert not table.referee.is_over

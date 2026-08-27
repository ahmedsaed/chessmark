"""One session per match (LOG-08).

The claim under test is not "the field is set" but "**a whole game is one session**": both seats,
every retry, every tool round-trip, and no other game's calls. That is the only property that
makes OpenRouter's own dashboard useful for debugging a match, and it is a property of the id we
derive rather than of anything we store.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.sessions import session_for_game
from chessmark.db.models import LlmCall
from chessmark.game import Colour
from tests.agents.conftest import Table, play_turn, seat

pytestmark = pytest.mark.integration


async def _sessions(db: AsyncSession, game_id: uuid.UUID) -> list[str | None]:
    rows = (
        await db.scalars(
            sa.select(LlmCall).where(LlmCall.game_id == game_id).order_by(LlmCall.created_at)
        )
    ).all()
    return [call.request.get("extra_body", {}).get("session_id") for call in rows]


def test_the_session_is_the_game() -> None:
    """Derived from `games.id` and prefixed, so it is recognisable on a dashboard belonging to an
    account that is used for other things too."""
    game_id = uuid.UUID("0f9b1c3e-1111-2222-3333-444455556666")
    assert session_for_game(game_id) == "game-0f9b1c3e-1111-2222-3333-444455556666"


async def test_both_seats_of_one_game_share_one_session(db: AsyncSession, table: Table) -> None:
    """The reason the unit is the game and not the seat. Half a chess game is not a conversation,
    and two sessions per match would mean opening two of them to read one game."""
    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))))
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )

    sessions = await _sessions(db, table.game.id)

    assert len(sessions) == 2
    assert set(sessions) == {session_for_game(table.game.id)}


async def test_every_call_of_a_turn_carries_it(db: AsyncSession, table: Table) -> None:
    """A turn is several round-trips — think, ask for the legal moves, move — and a session that
    covered only the first would break the chain exactly where a debugger needs it whole."""
    await play_turn(
        db,
        table,
        scripted(
            step(tool_call("get_legal_moves")),
            step(tool_call("get_board_state")),
            step(tool_call("make_move", move="e4")),
        ),
    )

    sessions = await _sessions(db, table.game.id)

    assert len(sessions) == 3
    assert set(sessions) == {session_for_game(table.game.id)}


async def test_two_games_are_two_sessions(db: AsyncSession, table: Table) -> None:
    """The other half of the property: a session that spanned games would group a hundred
    unrelated matches into one, which is what a tournament-level id would have done."""
    other = await seat(db)

    await play_turn(db, table, scripted(step(tool_call("make_move", move="e4"))))
    await play_turn(db, other, scripted(step(tool_call("make_move", move="d4"))))

    ours = await _sessions(db, table.game.id)
    theirs = await _sessions(db, other.game.id)

    assert ours == [session_for_game(table.game.id)]
    assert theirs == [session_for_game(other.game.id)]
    assert ours != theirs

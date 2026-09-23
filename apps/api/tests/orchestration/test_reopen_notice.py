"""Reopening a game tells the players it reopened.

**The failure this file exists for.** `8692cba1` reached the 300-ply cap, and the model's own
`make_move` result said so — `{"game_over": true, "result": "1/2-1/2", "termination": "ply_cap"}`.
It was reopened with a raised cap, which clears the ending from the *game record* and left the
*transcript* still saying the game had finished. The next turn asked Black to move at ply 302.
Black answered, four times, that the game had already ended "according to the terminal state
reported by the system" — and the harness forfeited it for not calling a tool, recording a rated
1-0 against a model that was doing exactly what its context told it to do.

That is a harness bound recorded as a finding about a player, which is what invariant 11 exists to
prevent, and the model was the only party in the exchange behaving correctly.

The correction is an **append**: the earlier "game over" stays where it is, because the transcript
is rows whose prefix must remain byte-identical for prompt caching (invariant 2), and a later
message is how a player would be told anything else.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.transcript import append_message
from chessmark.db.models import TranscriptMessage
from chessmark.game import Termination
from tests.orchestration.conftest import Fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_resume = importlib.import_module("resume_game")

pytestmark = pytest.mark.integration


async def _rows(db: AsyncSession, player_id) -> list[TranscriptMessage]:
    return list(
        await db.scalars(
            sa.select(TranscriptMessage)
            .where(TranscriptMessage.player_id == player_id)
            .order_by(TranscriptMessage.seq)
        )
    )


async def test_both_seats_are_told_the_game_is_live_again(db: AsyncSession, game: Fixture) -> None:
    """Both, not only the one to move.

    The ending was announced in whichever transcript happened to call `make_move` last; the
    opponent's next prompt is an ordinary "it is your move" that reads as normal. Two models given
    different accounts of the same game is how one of them ends up arguing with the referee.
    """
    row = await db.get(type(game.game), game.game.id)
    row.max_plies = 400
    row.termination_detail = "Draw — the 300-ply cap was reached."

    told = await _resume._tell_the_players(db, row, previous=Termination.PLY_CAP, ply=300)
    await db.flush()

    assert told == 2
    for player in (game.white, game.black):
        last = (await _rows(db, player.id))[-1]
        assert last.role == "user"
        assert "ply_cap" in last.content
        assert "300-ply cap was reached" in last.content, "it names the ending being set aside"
        assert "400" in last.content, "and the cap it is playing to now"
        assert "Disregard any earlier message" in last.content


async def test_the_notice_is_appended_and_changes_nothing_before_it(
    db: AsyncSession, game: Fixture
) -> None:
    """Invariant 2 in the one place a correction is tempted to edit history.

    Rewriting the "game over" row would be the obvious fix and would destroy the byte-stable prefix
    every cached turn depends on — for a game hundreds of turns long, which is exactly the kind
    that reaches a ply cap.
    """
    for content in ("system prompt", '{"game_over": true, "termination": "ply_cap"}'):
        await append_message(
            db, player_id=game.white.id, game_id=game.game.id, role="user", content=content
        )
    await db.flush()
    before = [(m.seq, m.role, m.content) for m in await _rows(db, game.white.id)]

    row = await db.get(type(game.game), game.game.id)
    await _resume._tell_the_players(db, row, previous=Termination.BUDGET_EXCEEDED, ply=42)
    await db.flush()

    after = await _rows(db, game.white.id)
    assert [(m.seq, m.role, m.content) for m in after[: len(before)]] == before
    assert len(after) == len(before) + 1
    assert after[-1].seq > before[-1][0]


async def test_a_game_that_never_ended_is_told_nothing(db: AsyncSession, game: Fixture) -> None:
    """A paused game's players were never told anything to correct, and a message that says the
    game is live again would be the first thing to suggest it had not been."""
    row = await db.get(type(game.game), game.game.id)
    before = len(await _rows(db, game.white.id))

    assert await _resume._tell_the_players(db, row, previous=None, ply=10) == 0
    assert len(await _rows(db, game.white.id)) == before

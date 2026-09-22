"""How people have done against the models (HUMAN-01).

The one number about human play the site could not state. Every game with a human seat is
unranked by definition — a person is not a contestant and never moves a rating — so the bench,
where all the other counting lives, has no reason to look at them and nothing else was.

Two things this pins, and they are the two that would be wrong if somebody rewrote the query:

* **The record is the person's**, not White's. A human playing Black who wins has won.
* **An unfinished game is not a defeat.** A game that was abandoned, aborted or is still running
  has decided nothing, and counting it against whoever was sitting there would be a finding about
  a player that the game never made (invariant 11).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import GameStatus, PlayerKind
from chessmark.db.models import Game
from chessmark.game import Colour, GameResult, Termination
from chessmark.orchestration.match import Seat, create_match

pytestmark = pytest.mark.integration


async def _played(
    db: AsyncSession,
    *,
    human: Colour,
    result: GameResult,
    status: GameStatus = GameStatus.FINISHED,
) -> Game:
    """One game with a person in a seat, left in whatever state the caller asked for."""
    you = Seat(display_name="you", kind=PlayerKind.HUMAN)
    machine = Seat(display_name="opponent", model="scripted/opponent")
    white, black = (you, machine) if human is Colour.WHITE else (machine, you)

    match = await create_match(db, white=white, black=black)
    game = match.game
    game.status = status
    game.result = result
    game.winner_colour = (
        Colour.WHITE
        if result is GameResult.WHITE_WINS
        else Colour.BLACK
        if result is GameResult.BLACK_WINS
        else None
    )
    game.termination = Termination.CHECKMATE if game.winner_colour else None
    await db.commit()
    return game


async def test_the_record_is_the_persons_side_of_the_board(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Two wins, one of them as Black — which the naive query reports as a loss."""
    await _played(db, human=Colour.WHITE, result=GameResult.WHITE_WINS)
    await _played(db, human=Colour.BLACK, result=GameResult.BLACK_WINS)
    await _played(db, human=Colour.WHITE, result=GameResult.BLACK_WINS)
    await _played(db, human=Colour.BLACK, result=GameResult.DRAW)

    body = (await client.get("/games/human-record")).json()

    assert body == {"games": 4, "wins": 2, "draws": 1, "losses": 1}


async def test_a_game_that_decided_nothing_is_not_a_loss(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Invariant 11, in the one place a person's record could quietly break it."""
    await _played(db, human=Colour.WHITE, result=GameResult.WHITE_WINS)
    await _played(db, human=Colour.WHITE, result=GameResult.ONGOING, status=GameStatus.RUNNING)
    await _played(db, human=Colour.BLACK, result=GameResult.ONGOING, status=GameStatus.ABORTED)

    body = (await client.get("/games/human-record")).json()

    assert body == {"games": 1, "wins": 1, "draws": 0, "losses": 0}


async def test_a_game_between_two_models_is_not_in_the_record(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The join is on a human seat, and the archive is almost entirely model against model."""
    match = await create_match(
        db,
        white=Seat(display_name="white", model="scripted/white"),
        black=Seat(display_name="black", model="scripted/black"),
    )
    match.game.status = GameStatus.FINISHED
    match.game.result = GameResult.WHITE_WINS
    match.game.winner_colour = Colour.WHITE
    await db.commit()

    assert (await client.get("/games/human-record")).json()["games"] == 0


async def test_the_record_costs_a_fixed_number_of_queries(
    client: AsyncClient, db: AsyncSession
) -> None:
    """**The regression this endpoint is one line away from.**

    Four integers computed by reading the games and tallying them in Python is a full scan of the
    archive on a route the landing page calls — the shape ADR-0032 removed from the leaderboard,
    and the one CLAUDE.md says to measure on the way in rather than audit into later. Counting
    statements rather than timing them: what this forbids is *growth*.
    """
    await _played(db, human=Colour.WHITE, result=GameResult.WHITE_WINS)

    statements: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        await client.get("/games/human-record")
        one_game = len(statements)

        statements.clear()
        for _ in range(6):
            await _played(db, human=Colour.BLACK, result=GameResult.DRAW)
        statements.clear()

        await client.get("/games/human-record")
        seven_games = len(statements)
    finally:
        sa.event.remove(db.bind.sync_engine, "before_cursor_execute", record)

    assert one_game <= 3, f"the record took {one_game} queries for one game"
    assert seven_games == one_game, (
        f"the record grew from {one_game} queries at one game to {seven_games} at seven — "
        "it is reading per game again"
    )

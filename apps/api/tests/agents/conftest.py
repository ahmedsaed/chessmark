"""Fixtures for agent-runtime tests.

Every test here runs a *real* turn — real tool dispatch, real referee, real persistence — with only
the provider replaced by a scripted double. That is the point: the thing under test is the turn
loop, not a mock of it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.llm import LlmGateway
from chessmark.agents.scripted import CompletionFn
from chessmark.agents.turn import TurnLimits, TurnResult, TurnRunner, ensure_system_prompt
from chessmark.db.enums import PlayerKind
from chessmark.db.models import Game, Player
from chessmark.db.repositories import add_player, create_game
from chessmark.game import ChessBoard, Colour, Referee

STARTING_FEN = ChessBoard().fen


@dataclass(slots=True)
class Table:
    """A game with two seated players, and the referee holding the live position."""

    game: Game
    white: Player
    black: Player
    referee: Referee

    def player(self, colour: Colour) -> Player:
        return self.white if colour is Colour.WHITE else self.black

    def opponent_name(self, colour: Colour) -> str:
        return self.player(colour.opponent).display_name


async def seat(
    db: AsyncSession,
    *,
    start_fen: str = STARTING_FEN,
    trash_talk_enabled: bool = True,
    max_illegal_retries: int = 5,
    max_plies: int = 300,
) -> Table:
    game = await create_game(
        db,
        start_fen=start_fen,
        trash_talk_enabled=trash_talk_enabled,
        max_illegal_retries=max_illegal_retries,
        max_plies=max_plies,
    )
    white = await add_player(
        db,
        game_id=game.id,
        colour=Colour.WHITE,
        kind=PlayerKind.MODEL,
        display_name="white-model",
    )
    black = await add_player(
        db,
        game_id=game.id,
        colour=Colour.BLACK,
        kind=PlayerKind.MODEL,
        display_name="black-model",
    )
    await db.commit()

    return Table(
        game=game,
        white=white,
        black=black,
        referee=Referee(start_fen=start_fen, max_plies=max_plies),
    )


@pytest.fixture
async def table(db: AsyncSession) -> Table:
    return await seat(db)


async def play_turn(
    db: AsyncSession,
    table: Table,
    completion_fn: CompletionFn,
    *,
    colour: Colour = Colour.WHITE,
    limits: TurnLimits | None = None,
    model: str = "scripted/model",
) -> TurnResult:
    """Run one whole turn with a scripted model, exactly as the worker will."""
    player = table.player(colour)
    opponent = table.player(colour.opponent)

    await ensure_system_prompt(
        db, game=table.game, player=player, opponent_name=table.opponent_name(colour)
    )

    runner = TurnRunner(
        db,
        gateway=LlmGateway(completion_fn=completion_fn),
        referee=table.referee,
        game=table.game,
        player=player,
        opponent=opponent,
        model=model,
        limits=limits,
    )
    result = await runner.run()
    await db.commit()
    return result


@pytest.fixture
async def db_human_table(db: AsyncSession) -> Table:
    """A game with a human in the black seat."""
    table = await seat(db)
    await db.execute(
        sa.update(Player).where(Player.id == table.black.id).values(kind=PlayerKind.HUMAN)
    )
    await db.commit()
    await db.refresh(table.black)
    return table

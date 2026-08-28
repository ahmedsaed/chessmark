"""Data access for games and their history.

Deliberately functions over a session rather than repository classes — there is no state to hold,
and it keeps the transaction boundary in the caller where NFR-08 needs it.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.enums import EventType, GameStatus, PlayerKind
from chessmark.db.models import Game, GameEvent, Player, Ply
from chessmark.game import Colour, GameResult, MoveOutcome, Outcome, Referee


class GameNotFoundError(LookupError):
    def __init__(self, game_id: uuid.UUID) -> None:
        super().__init__(f"no game with id {game_id}")
        self.game_id = game_id


# ---------------------------------------------------------------------- games


async def create_game(
    session: AsyncSession,
    *,
    start_fen: str,
    is_ranked: bool = False,
    trash_talk_enabled: bool = True,
    max_illegal_retries: int = 5,
    max_plies: int = 300,
    prompt_version: str | None = None,
    tool_schema_version: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
    max_usd: Any = None,
) -> Game:
    game = Game(
        start_fen=start_fen,
        status=GameStatus.PENDING,
        is_ranked=is_ranked,
        trash_talk_enabled=trash_talk_enabled,
        max_illegal_retries=max_illegal_retries,
        max_plies=max_plies,
        prompt_version=prompt_version,
        tool_schema_version=tool_schema_version,
        created_by_user_id=created_by_user_id,
        max_usd=max_usd,
    )
    session.add(game)
    await session.flush()
    return game


async def get_game(session: AsyncSession, game_id: uuid.UUID) -> Game:
    game = await session.get(Game, game_id)
    if game is None:
        raise GameNotFoundError(game_id)
    return game


async def add_player(
    session: AsyncSession,
    *,
    game_id: uuid.UUID,
    colour: Colour,
    kind: PlayerKind,
    display_name: str,
    model_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    persona: str | None = None,
    system_prompt_version: str | None = None,
    sampling: dict[str, Any] | None = None,
) -> Player:
    player = Player(
        game_id=game_id,
        colour=colour,
        kind=kind,
        display_name=display_name,
        model_id=model_id,
        user_id=user_id,
        persona=persona,
        system_prompt_version=system_prompt_version,
        sampling=sampling or {},
    )
    session.add(player)
    await session.flush()
    return player


async def finish_game(session: AsyncSession, *, game_id: uuid.UUID, outcome: Outcome) -> Game:
    game = await get_game(session, game_id)
    game.status = GameStatus.FINISHED
    game.result = outcome.result
    game.termination = outcome.termination
    game.termination_detail = outcome.detail
    game.winner_colour = outcome.winner
    game.ended_at = sa.func.now()
    await session.flush()
    return game


# ---------------------------------------------------------------------- plies


async def record_ply(
    session: AsyncSession,
    *,
    game_id: uuid.UUID,
    colour: Colour,
    move: MoveOutcome,
    turn_id: int | None = None,
    think_ms: int | None = None,
) -> Ply:
    """Append a committed half-move and advance the game's ply counter.

    The uniqueness of `(game_id, ply_number)` is what makes a redelivered turn job harmless
    (ADR-0007): a duplicate insert fails rather than corrupting the record.
    """
    ply = Ply(
        game_id=game_id,
        turn_id=turn_id,
        ply_number=move.ply,
        colour=colour,
        san=move.move.san,
        uci=move.move.uci,
        fen_before=move.fen_before,
        fen_after=move.fen_after,
        is_capture=move.move.is_capture,
        is_check=move.move.is_check,
        is_checkmate=move.move.is_checkmate,
        is_castling=move.move.is_castling,
        is_en_passant=move.move.is_en_passant,
        promotion=move.move.promotion,
        think_ms=think_ms,
    )
    session.add(ply)

    await session.execute(sa.update(Game).where(Game.id == game_id).values(ply_count=move.ply))
    await session.flush()
    return ply


async def load_plies(session: AsyncSession, game_id: uuid.UUID) -> list[Ply]:
    result = await session.scalars(
        sa.select(Ply).where(Ply.game_id == game_id).order_by(Ply.ply_number)
    )
    return list(result)


async def load_moves_san(session: AsyncSession, game_id: uuid.UUID) -> list[str]:
    """The move list, in order. Enough to reconstruct the whole game from `start_fen`."""
    result = await session.scalars(
        sa.select(Ply.san).where(Ply.game_id == game_id).order_by(Ply.ply_number)
    )
    return list(result)


# ---------------------------------------------------------------------- events


async def append_event(
    session: AsyncSession,
    *,
    game_id: uuid.UUID,
    type: EventType,
    payload: dict[str, Any] | None = None,
) -> GameEvent:
    """Append to the game's event log with a gap-free sequence number.

    `UPDATE ... RETURNING` on the parent row takes a row lock held until the transaction commits,
    so concurrent appends to the same game serialise here rather than racing to compute
    `MAX(seq) + 1`. The unique constraint on `(game_id, seq)` is the backstop if this is ever
    bypassed.
    """
    seq = await session.scalar(
        sa.update(Game)
        .where(Game.id == game_id)
        .values(event_seq=Game.event_seq + 1)
        .returning(Game.event_seq)
    )
    if seq is None:
        raise GameNotFoundError(game_id)

    event = GameEvent(game_id=game_id, seq=seq, type=type, payload=payload or {})
    session.add(event)
    await session.flush()
    return event


async def load_events(
    session: AsyncSession,
    game_id: uuid.UUID,
    *,
    after_seq: int = 0,
    limit: int | None = None,
) -> list[GameEvent]:
    """Events in order, optionally from a cursor.

    `after_seq` is what backs SSE reconnect: the client sends `Last-Event-ID`, we replay exactly
    what it missed, then attach to the live stream (UI-10).
    """
    query = (
        sa.select(GameEvent)
        .where(GameEvent.game_id == game_id, GameEvent.seq > after_seq)
        .order_by(GameEvent.seq)
    )
    if limit is not None:
        query = query.limit(limit)

    result = await session.scalars(query)
    return list(result)


# ---------------------------------------------------------------------- listings


async def list_games(
    session: AsyncSession,
    *,
    status: GameStatus | None = None,
    limit: int = 50,
) -> list[Game]:
    query = sa.select(Game).order_by(Game.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Game.status == status)

    result = await session.scalars(query)
    return list(result)


async def count_games_by_result(session: AsyncSession) -> dict[GameResult, int]:
    rows = await session.execute(sa.select(Game.result, sa.func.count()).group_by(Game.result))
    return {result: count for result, count in rows.all()}  # noqa: C416 - Row is not a tuple


async def rebuild_referee(session: AsyncSession, game: Game) -> Referee:
    """Reconstruct the live position by replaying stored plies.

    Workers are stateless (ADR-0007), so every turn starts by rebuilding from Postgres rather than
    carrying a board between jobs. Replaying also re-derives terminal state, so a game that ended
    in checkmate rebuilds as over.

    Terminations that are not moves — resignation, forfeit, adjudication — are not in the ply
    record, so they are re-applied from the stored outcome afterwards.
    """
    referee = Referee(
        start_fen=game.start_fen,
        max_plies=game.max_plies,
        # Read from the game, so a rebuilt referee applies the same rules the game was created
        # under. These columns existed from Phase 1 and were never read by anything — a game
        # asking not to be auto-drawn was auto-drawn anyway.
        auto_threefold_draw=game.auto_threefold_draw,
        auto_fifty_move_draw=game.auto_fifty_move_draw,
    )
    for san in await load_moves_san(session, game.id):
        referee.play(san)

    if game.status is GameStatus.FINISHED and not referee.is_over and game.termination:
        referee.adjudicate(
            game.result,
            game.termination_detail or "",
            termination=game.termination,
        )

    return referee

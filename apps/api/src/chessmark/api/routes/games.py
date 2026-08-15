"""Game endpoints.

All read paths are unauthenticated by design (AUTH-02): spectating and replays are the shareable
surface, and requiring an account to watch would defeat the point. Creating a game spends money
and is gated in Phase 9.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from chessmark.api.deps import GameDep, QueueDep, SessionDep
from chessmark.api.schemas import (
    CreateGameRequest,
    CreateGameResponse,
    EventOut,
    GameDetail,
    GameSummary,
    MessageOut,
    PlyOut,
    TurnDetail,
)
from chessmark.db.enums import GameStatus, ModerationStatus
from chessmark.db.models import (
    Game,
    LlmCall,
    Message,
    ModelEndpoint,
    ModelRegistry,
    Player,
    Ply,
    ToolCall,
    Turn,
)
from chessmark.db.repositories import load_events, rebuild_referee
from chessmark.orchestration.match import Seat, create_match, start_match

router = APIRouter(prefix="/games", tags=["games"])


async def _players(session: SessionDep, game_id: uuid.UUID) -> list[Player]:
    return list(await session.scalars(sa.select(Player).where(Player.game_id == game_id)))


async def _served_by(
    session: SessionDep, game_id: uuid.UUID
) -> dict[uuid.UUID, tuple[list[str], str | None]]:
    """Which endpoints actually served each seat, and at what precision.

    The chat response names the provider; `model_endpoints` supplies the quantization. Together
    they answer the question a leaderboard row is meaningless without — not "which model" but
    "which model, served how".
    """
    rows = (
        await session.execute(
            sa.select(Turn.player_id, LlmCall.provider, ModelEndpoint.quantization)
            .join(LlmCall, LlmCall.turn_id == Turn.id)
            .join(ModelRegistry, ModelRegistry.openrouter_id == LlmCall.model_slug, isouter=True)
            .join(
                ModelEndpoint,
                sa.and_(
                    ModelEndpoint.model_id == ModelRegistry.id,
                    ModelEndpoint.provider_name == LlmCall.provider,
                ),
                isouter=True,
            )
            .where(Turn.game_id == game_id, LlmCall.provider.is_not(None))
            .distinct()
        )
    ).all()

    served: dict[uuid.UUID, tuple[list[str], str | None]] = {}
    for player_id, provider, quantization in rows:
        providers, quant = served.get(player_id, ([], None))
        if provider and provider not in providers:
            providers.append(provider)
        served[player_id] = (providers, quant or quantization)
    return served


def _reveal_reasoning(game: Game) -> bool:
    """Reasoning is withheld until the game is over (invariant 8, HUMAN-07).

    Mid-game it would leak a model's plan to its opponent — or, in a human game, to the human.
    """
    return game.status in {GameStatus.FINISHED, GameStatus.ABORTED}


# ---------------------------------------------------------------------- listing


@router.get("", response_model=list[GameSummary])
async def list_games(
    session: SessionDep,
    status_filter: Annotated[GameStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[GameSummary]:
    query = sa.select(Game).order_by(Game.created_at.desc()).limit(limit)
    if status_filter is not None:
        query = query.where(Game.status == status_filter)

    games = list(await session.scalars(query))
    if not games:
        return []

    ids = [game.id for game in games]
    players = list(await session.scalars(sa.select(Player).where(Player.game_id.in_(ids))))
    by_game: dict[uuid.UUID, list[Player]] = {}
    for player in players:
        by_game.setdefault(player.game_id, []).append(player)

    return [GameSummary.from_model(game, by_game.get(game.id, [])) for game in games]


@router.get("/{game_id}", response_model=GameDetail)
async def get_game_detail(session: SessionDep, game: GameDep) -> GameDetail:
    referee = await rebuild_referee(session, game)
    return GameDetail.from_model(
        game,
        await _players(session, game.id),
        moves=referee.board.history_san(),
        current_fen=referee.board.fen,
        served_by=await _served_by(session, game.id),
    )


@router.get("/{game_id}/plies", response_model=list[PlyOut])
async def get_plies(session: SessionDep, game: GameDep) -> list[PlyOut]:
    rows = await session.scalars(
        sa.select(Ply).where(Ply.game_id == game.id).order_by(Ply.ply_number)
    )
    return [PlyOut.from_model(row) for row in rows]


@router.get("/{game_id}/messages", response_model=list[MessageOut])
async def get_messages(session: SessionDep, game: GameDep) -> list[MessageOut]:
    """Trash talk. Blocked messages are withheld from display but remain stored (TALK-05)."""
    rows = await session.scalars(
        sa.select(Message)
        .where(
            Message.game_id == game.id,
            Message.moderation_status != ModerationStatus.BLOCKED,
        )
        .order_by(Message.id)
    )
    return [MessageOut.model_validate(row) for row in rows]


@router.get("/{game_id}/turns", response_model=list[TurnDetail])
async def get_turns(session: SessionDep, game: GameDep) -> list[TurnDetail]:
    """Every turn with its LLM and tool calls.

    Reasoning traces are omitted while the game is live; `reasoning_available` says which it is,
    so a client can show "revealed after the game" rather than an unexplained blank.
    """
    reveal = _reveal_reasoning(game)

    turns = list(
        await session.scalars(sa.select(Turn).where(Turn.game_id == game.id).order_by(Turn.id))
    )
    if not turns:
        return []

    turn_ids = [turn.id for turn in turns]
    llm_rows = list(
        await session.scalars(
            sa.select(LlmCall).where(LlmCall.turn_id.in_(turn_ids)).order_by(LlmCall.sequence)
        )
    )
    tool_rows = list(
        await session.scalars(
            sa.select(ToolCall).where(ToolCall.turn_id.in_(turn_ids)).order_by(ToolCall.sequence)
        )
    )

    llm_by_turn: dict[int, list[LlmCall]] = {}
    for llm_row in llm_rows:
        llm_by_turn.setdefault(llm_row.turn_id, []).append(llm_row)
    tool_by_turn: dict[int, list[ToolCall]] = {}
    for tool_row in tool_rows:
        tool_by_turn.setdefault(tool_row.turn_id, []).append(tool_row)

    return [
        TurnDetail.from_model(
            turn,
            llm_calls=llm_by_turn.get(turn.id, []),
            tool_calls=tool_by_turn.get(turn.id, []),
            reveal_reasoning=reveal,
        )
        for turn in turns
    ]


@router.get("/{game_id}/events", response_model=list[EventOut])
async def get_event_log(
    session: SessionDep,
    game: GameDep,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> list[EventOut]:
    """The event log as a plain list — the replay path (ADR-0008).

    The same rows the SSE stream delivers live, which is what keeps live and replay consistent.
    """
    events = await load_events(session, game.id, after_seq=after_seq, limit=limit)
    return [EventOut.from_model(event) for event in events]


# ---------------------------------------------------------------------- creation


@router.post("", response_model=CreateGameResponse, status_code=status.HTTP_201_CREATED)
async def create_game_endpoint(
    session: SessionDep, queue: QueueDep, request: CreateGameRequest
) -> CreateGameResponse:
    """Start a model-vs-model game.

    **Unauthenticated and unmetered until Phase 9**, which is a hard gate before any public
    deploy — this spends money and there is no per-user quota behind it yet (ADR-0011).
    """
    known = {
        row.openrouter_id: row
        for row in await session.scalars(
            sa.select(ModelRegistry).where(
                ModelRegistry.openrouter_id.in_([request.white, request.black])
            )
        )
    }

    for slug in (request.white, request.black):
        model = known.get(slug)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown model {slug!r}. See GET /models for what is playable.",
            )
        if not model.supports_tools:
            # AGENT-01: agents act only through tools, so a model without them cannot play at all.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{slug!r} does not support tool calling and cannot play.",
            )
        if not model.enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"{slug!r} is disabled."
            )

    kwargs: dict[str, Any] = {}
    if request.start_fen:
        kwargs["start_fen"] = request.start_fen

    match = await create_match(
        session,
        white=Seat(display_name=known[request.white].display_name, model=request.white),
        black=Seat(display_name=known[request.black].display_name, model=request.black),
        is_ranked=request.is_ranked,
        trash_talk_enabled=request.trash_talk_enabled,
        max_usd=request.max_usd,
        max_plies=request.max_plies,
        **kwargs,
    )
    job = await start_match(session, queue, game_id=match.game.id)
    await session.commit()

    # Enqueued only after the commit: a worker must never be handed a game that a rolled-back
    # transaction means does not exist.
    await queue.enqueue(job)

    return CreateGameResponse(
        id=match.game.id,
        status=GameStatus.RUNNING,
        events_url=f"/games/{match.game.id}/stream",
    )

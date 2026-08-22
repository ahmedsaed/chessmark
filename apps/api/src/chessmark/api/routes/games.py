"""Game endpoints.

All read paths are unauthenticated by design (AUTH-02): spectating and replays are the shareable
surface, and requiring an account to watch would defeat the point. Creating a game spends money
and is gated in Phase 9.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import PlainTextResponse

from chessmark.api.deps import (
    BudgetDep,
    CurrentUser,
    GameDep,
    QueueDep,
    SessionDep,
    SettingsDep,
    enforce_rate_limit,
)
from chessmark.api.schemas import (
    CreateGameRequest,
    CreateGameResponse,
    EventOut,
    GameDetail,
    GameSummary,
    MessageOut,
    PlyOut,
    RawCallOut,
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
from chessmark.db.quotas import QuotaExceededError, reserve_game
from chessmark.db.repositories import load_events, rebuild_referee
from chessmark.game import Colour
from chessmark.game.pgn import PgnMetadata, to_pgn
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


# ---------------------------------------------------------------------- artefacts


def _display(players: list[Player], colour: Colour) -> str:
    for player in players:
        if player.colour is colour:
            return player.display_name
    return str(colour)


def _illegal(players: list[Player], colour: Colour) -> int | None:
    for player in players:
        if player.colour is colour:
            return player.illegal_attempts
    return None


@router.get(
    "/{game_id}/pgn",
    response_class=PlainTextResponse,
    responses={200: {"content": {"application/x-chess-pgn": {}}}},
)
async def get_pgn(session: SessionDep, game: GameDep) -> PlainTextResponse:
    """The game as PGN (GAME-05).

    Exported from the ply record via the referee rather than from a stored string, so the file can
    never disagree with the position the server considers authoritative. An unfinished game
    exports with a `*` result, which is legal PGN — there is no reason to refuse it.

    Served as a download with a filename, because the point of this endpoint is that someone opens
    the result in Lichess or SCID.
    """
    referee = await rebuild_referee(session, game)
    players = await _players(session, game.id)

    pgn = to_pgn(
        referee,
        PgnMetadata(
            white=_display(players, Colour.WHITE),
            black=_display(players, Colour.BLACK),
            game_id=str(game.id),
            date=(game.started_at or game.created_at).strftime("%Y.%m.%d"),
            ranked=game.is_ranked,
            prompt_version=game.prompt_version,
            tool_schema_version=game.tool_schema_version,
            white_illegal_attempts=_illegal(players, Colour.WHITE),
            black_illegal_attempts=_illegal(players, Colour.BLACK),
        ),
    )

    return PlainTextResponse(
        pgn,
        media_type="application/x-chess-pgn",
        headers={
            "content-disposition": f'attachment; filename="chessmark-{game.id}.pgn"',
        },
    )


@router.get("/{game_id}/turns/{turn_id}/raw", response_model=list[RawCallOut])
async def get_raw_calls(
    session: SessionDep,
    game: GameDep,
    turn_id: Annotated[int, Path(description="Turn id, from /turns")],
) -> list[RawCallOut]:
    """The verbatim request and response behind one turn (LOG-01, LOG-07).

    This is the bottom of the audit trail: every number on a game page is derived from these
    payloads, and a benchmark whose figures cannot be traced to what the provider actually
    returned is asking to be taken on faith. Secrets are redacted at write time, not here — a key
    that reached the database is already leaked (see `agents/redaction.py`).

    **Withheld while the game is live** (invariant 8). The raw response carries the reasoning
    trace, so serving it mid-game would route around the very rule `/turns` enforces.
    """
    if not _reveal_reasoning(game):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Raw transcripts are available once the game has ended.",
        )

    # Scoped to the game in the query rather than checked afterwards, so a turn id from another
    # game reads as absent instead of leaking whether it exists.
    calls = list(
        await session.scalars(
            sa.select(LlmCall)
            .join(Turn, Turn.id == LlmCall.turn_id)
            .where(Turn.game_id == game.id, LlmCall.turn_id == turn_id)
            .order_by(LlmCall.sequence)
        )
    )
    if not calls:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No turn {turn_id} in game {game.id}",
        )

    return [RawCallOut.from_model(call) for call in calls]


# ---------------------------------------------------------------------- creation


@router.post(
    "",
    response_model=CreateGameResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_rate_limit)],
)
async def create_game_endpoint(
    session: SessionDep,
    queue: QueueDep,
    budget: BudgetDep,
    settings: SettingsDep,
    user: CurrentUser,
    request: CreateGameRequest,
) -> CreateGameResponse:
    """Start a model-vs-model game.

    The only endpoint on this router that requires an account, because it is the only one that
    spends money (AUTH-02). Three of ADR-0011's four layers are applied here, outermost first:
    the rate limiter, the global kill switch, then the user's daily quota. The fourth — the
    per-game cap — is carried on the game itself and enforced by the worker.

    Order matters. The kill switch is checked before the quota so that a user is not charged a
    game against their daily allowance for a request that was never going to run.
    """
    if await budget.tripped():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Chessmark has reached its daily spend limit. New games resume at UTC midnight; "
                "watching and replays are unaffected."
            ),
        )
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

    try:
        await reserve_game(
            session,
            user.id,
            max_games=settings.max_games_per_user_per_day,
            max_usd=Decimal(str(settings.max_usd_per_user_per_day)),
        )
    except QuotaExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{error} Quotas reset at UTC midnight.",
        ) from error

    kwargs: dict[str, Any] = {}
    if request.start_fen:
        kwargs["start_fen"] = request.start_fen

    # The per-game cap is never left to the caller alone: a request asking for more than the
    # server's ceiling is clamped rather than refused, so an ambitious `max_usd` cannot become the
    # budget. This is layer 3 of ADR-0011.
    ceiling = Decimal(str(settings.max_usd_per_game))
    max_usd = min(request.max_usd, ceiling) if request.max_usd else ceiling

    match = await create_match(
        session,
        white=Seat(display_name=known[request.white].display_name, model=request.white),
        black=Seat(display_name=known[request.black].display_name, model=request.black),
        is_ranked=request.is_ranked,
        trash_talk_enabled=request.trash_talk_enabled,
        max_usd=max_usd,
        max_plies=request.max_plies,
        created_by_user_id=user.id,
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

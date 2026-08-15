"""Response shapes for the HTTP API.

Two rules run through all of this:

* **Money is a string.** `Decimal` serialises to a JSON string, not a float. Costs run to eight
  decimal places and a float would round them at exactly the scale invariant 4 cares about.
* **Reasoning is never exposed mid-game.** A live game's turn detail omits it entirely — see
  `TurnDetail.from_model`. It would leak a model's plan to its opponent or to a human player
  (invariant 8, HUMAN-07).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chessmark.db.enums import EventType, GameStatus, PlayerKind, TurnStatus
from chessmark.db.models import (
    Game,
    GameEvent,
    LlmCall,
    ModelRegistry,
    Player,
    Ply,
    ToolCall,
    Turn,
)
from chessmark.game import Colour, GameResult, Termination


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------- models


class ModelOut(Schema):
    id: uuid.UUID
    openrouter_id: str
    display_name: str
    provider: str
    context_length: int | None
    supports_reasoning: bool
    is_free: bool
    prompt_usd_per_token: Decimal
    completion_usd_per_token: Decimal

    @classmethod
    def from_model(cls, row: ModelRegistry) -> ModelOut:
        return cls.model_validate(row)


# ---------------------------------------------------------------------- players


class PlayerOut(Schema):
    id: uuid.UUID
    colour: Colour
    kind: PlayerKind
    display_name: str
    model: str | None = None
    persona: str | None = None

    illegal_attempts: int
    forfeited: bool
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    total_cost_usd: Decimal

    @classmethod
    def from_model(cls, row: Player) -> PlayerOut:
        model = (row.sampling or {}).get("model")
        return cls(
            id=row.id,
            colour=row.colour,
            kind=row.kind,
            display_name=row.display_name,
            model=str(model) if model else None,
            persona=row.persona,
            illegal_attempts=row.illegal_attempts,
            forfeited=row.forfeited,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            reasoning_tokens=row.reasoning_tokens,
            cached_tokens=row.cached_tokens,
            total_cost_usd=row.total_cost_usd,
        )


# ---------------------------------------------------------------------- plies


class PlyOut(Schema):
    ply_number: int
    colour: Colour
    san: str
    uci: str
    fen_after: str
    is_capture: bool
    is_check: bool
    is_checkmate: bool
    is_castling: bool
    is_en_passant: bool
    promotion: str | None
    think_ms: int | None

    #: Null until the Phase 14 analysis worker fills them in (BENCH-06).
    eval_after_cp: int | None = None
    cp_loss: int | None = None
    classification: str | None = None

    @classmethod
    def from_model(cls, row: Ply) -> PlyOut:
        return cls.model_validate(row)


# ---------------------------------------------------------------------- games


class GameSummary(Schema):
    id: uuid.UUID
    status: GameStatus
    result: GameResult
    termination: Termination | None
    winner_colour: Colour | None
    ply_count: int
    is_ranked: bool
    trash_talk_enabled: bool
    total_cost_usd: Decimal
    total_tokens: int
    created_at: dt.datetime
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    players: list[PlayerOut] = Field(default_factory=list)

    @classmethod
    def from_model(cls, game: Game, players: list[Player]) -> GameSummary:
        return cls(
            id=game.id,
            status=game.status,
            result=game.result,
            termination=game.termination,
            winner_colour=game.winner_colour,
            ply_count=game.ply_count,
            is_ranked=game.is_ranked,
            trash_talk_enabled=game.trash_talk_enabled,
            total_cost_usd=game.total_cost_usd,
            total_tokens=game.total_tokens,
            created_at=game.created_at,
            started_at=game.started_at,
            ended_at=game.ended_at,
            players=sorted(
                (PlayerOut.from_model(p) for p in players), key=lambda p: p.colour.value
            ),
        )


class GameDetail(GameSummary):
    start_fen: str
    current_fen: str
    termination_detail: str | None
    prompt_version: str | None
    tool_schema_version: str | None
    max_usd: Decimal | None
    max_illegal_retries: int
    max_plies: int
    event_seq: int
    """The highest event sequence yet emitted — a client's starting cursor for SSE."""

    moves: list[str] = Field(default_factory=list)

    @classmethod
    def from_model(  # type: ignore[override]
        cls,
        game: Game,
        players: list[Player],
        *,
        moves: list[str],
        current_fen: str,
    ) -> GameDetail:
        summary = GameSummary.from_model(game, players)
        return cls(
            **summary.model_dump(),
            start_fen=game.start_fen,
            current_fen=current_fen,
            termination_detail=game.termination_detail,
            prompt_version=game.prompt_version,
            tool_schema_version=game.tool_schema_version,
            max_usd=game.max_usd,
            max_illegal_retries=game.max_illegal_retries,
            max_plies=game.max_plies,
            event_seq=game.event_seq,
            moves=moves,
        )


class CreateGameRequest(BaseModel):
    """Start a model-vs-model game.

    Unauthenticated until Phase 9, which is a hard gate before any public deploy — this endpoint
    spends money and has no quota behind it yet (ADR-0011).
    """

    white: str = Field(description="OpenRouter model id for White")
    black: str = Field(description="OpenRouter model id for Black")
    is_ranked: bool = False
    trash_talk_enabled: bool = True
    max_usd: Decimal | None = Field(default=Decimal("0.50"), ge=0)
    max_plies: int = Field(default=300, ge=2, le=1000)
    start_fen: str | None = None


class CreateGameResponse(Schema):
    id: uuid.UUID
    status: GameStatus
    events_url: str


# ---------------------------------------------------------------------- messages


class MessageOut(Schema):
    id: int
    player_id: uuid.UUID | None
    ply_number: int | None
    content: str
    created_at: dt.datetime


# ---------------------------------------------------------------------- turns


class ToolCallOut(Schema):
    sequence: int
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    ok: bool
    duration_ms: int | None

    @classmethod
    def from_model(cls, row: ToolCall) -> ToolCallOut:
        return cls.model_validate(row)


class LlmCallOut(Schema):
    sequence: int
    model_slug: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    cost_usd: Decimal
    latency_ms: int | None
    finish_reason: str | None
    error: str | None

    #: Withheld while the game is live (invariant 8). Populated only after it ends.
    reasoning: str | None = None

    @classmethod
    def from_model(cls, row: LlmCall, *, reveal_reasoning: bool) -> LlmCallOut:
        return cls(
            sequence=row.sequence,
            model_slug=row.model_slug,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            reasoning_tokens=row.reasoning_tokens,
            cached_tokens=row.cached_tokens,
            cost_usd=row.cost_usd,
            latency_ms=row.latency_ms,
            finish_reason=row.finish_reason,
            error=row.error,
            reasoning=row.reasoning_text if reveal_reasoning else None,
        )


class TurnDetail(Schema):
    id: int
    player_id: uuid.UUID
    ply_number: int | None
    status: TurnStatus
    illegal_attempts: int
    tool_call_count: int
    llm_call_count: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    cost_usd: Decimal
    latency_ms: int | None
    error: str | None
    started_at: dt.datetime
    ended_at: dt.datetime | None

    reasoning_available: bool = False
    """False while the game is live. The traces exist; they are simply withheld."""

    llm_calls: list[LlmCallOut] = Field(default_factory=list)
    tool_calls: list[ToolCallOut] = Field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        turn: Turn,
        *,
        llm_calls: list[LlmCall],
        tool_calls: list[ToolCall],
        reveal_reasoning: bool,
    ) -> TurnDetail:
        return cls(
            id=turn.id,
            player_id=turn.player_id,
            ply_number=turn.ply_number,
            status=turn.status,
            illegal_attempts=turn.illegal_attempts,
            tool_call_count=turn.tool_call_count,
            llm_call_count=turn.llm_call_count,
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
            reasoning_tokens=turn.reasoning_tokens,
            cached_tokens=turn.cached_tokens,
            cost_usd=turn.cost_usd,
            latency_ms=turn.latency_ms,
            error=turn.error,
            started_at=turn.started_at,
            ended_at=turn.ended_at,
            reasoning_available=reveal_reasoning,
            llm_calls=[
                LlmCallOut.from_model(call, reveal_reasoning=reveal_reasoning) for call in llm_calls
            ],
            tool_calls=[ToolCallOut.from_model(call) for call in tool_calls],
        )


# ---------------------------------------------------------------------- events


class EventOut(Schema):
    seq: int
    type: EventType
    payload: dict[str, Any]
    created_at: dt.datetime

    @classmethod
    def from_model(cls, row: GameEvent) -> EventOut:
        return cls.model_validate(row)


# ---------------------------------------------------------------------- health


class HealthResponse(Schema):
    status: str
    version: str


class ReadinessResponse(Schema):
    status: str
    database: bool
    redis: bool

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

from chessmark.agents.registry import is_floating_alias
from chessmark.db.enums import EventType, GameStatus, PlayerKind, TurnStatus
from chessmark.db.models import (
    Game,
    GameEvent,
    LlmCall,
    ModelEndpoint,
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


class ContestantOut(Schema):
    """One precision a model can be played at, and the endpoint that would serve it.

    Deliberately does **not** carry OpenRouter's `supports_implicit_caching`. That flag is stored
    on `model_endpoints` as a record of what the API said, but it does not predict behaviour: it
    reads `false` for endpoints we have measured at 91-94% cache hit rate (Azure/gpt-5.4-mini,
    Baidu/deepseek-v4-flash, StreamLake/kimi-k2.5) and `true` for the one measured at 28%
    (Google/gemini-3.7-flash). Publishing it would mislead more often than it informed.

    A contestant, not a capability (ADR-0015). `model@fp4` and `model@fp8` are different entrants
    and are ranked apart, so each gets its own row here rather than the model carrying a list of
    "allowed" precisions — which was filter vocabulary for a policy that no longer exists.
    """

    quantization: str
    provider: str
    """The endpoint a match would pin for this contestant, chosen by uptime."""

    uptime_1d: float | None = None

    endpoint_count: int = 1
    """How many endpoints serve this precision. One means an outage takes the contestant with it."""


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

    #: Every precision this model is served at. Kept for continuity; `contestants` is the useful
    #: shape now, because it says *which endpoint* each precision would actually run on.
    quantizations: list[str] = Field(default_factory=list)

    #: One entry per precision that can be played, healthiest endpoint first (ADR-0015).
    contestants: list[ContestantOut] = Field(default_factory=list)
    endpoint_count: int = 0

    #: Floating aliases point at different weights over time, so a rating across one rates nothing.
    is_floating_alias: bool = False

    #: What a seat against this model costs to start (ADR-0016). The picker shows it, because with
    #: 330 models spanning a 300-fold price range a name alone is not enough to choose on.
    credit_cost: int = 1

    @classmethod
    def from_model(
        cls, row: ModelRegistry, *, endpoints: list[ModelEndpoint] | None = None
    ) -> ModelOut:
        endpoints = endpoints or []
        playable = [e for e in endpoints if e.is_active and e.supports_tools]

        by_precision: dict[str, list[ModelEndpoint]] = {}
        for endpoint in playable:
            by_precision.setdefault(endpoint.quantization or "unknown", []).append(endpoint)

        contestants = []
        for quantization, group in by_precision.items():
            # Same order the match uses, so the card names the endpoint a game would really pin.
            best = sorted(
                group,
                key=lambda e: (
                    -(e.uptime_1d if e.uptime_1d is not None else (e.uptime_30m or -1.0)),
                    -(e.throughput or -1.0),
                    e.provider_name,
                ),
            )[0]
            contestants.append(
                ContestantOut(
                    quantization=quantization,
                    provider=best.provider_name,
                    uptime_1d=best.uptime_1d,
                    endpoint_count=len(group),
                )
            )

        contestants.sort(key=lambda c: (-(c.uptime_1d or -1.0), c.quantization))

        return cls(
            id=row.id,
            openrouter_id=row.openrouter_id,
            display_name=row.display_name,
            provider=row.provider,
            context_length=row.context_length,
            supports_reasoning=row.supports_reasoning,
            is_free=row.is_free,
            prompt_usd_per_token=row.prompt_usd_per_token,
            completion_usd_per_token=row.completion_usd_per_token,
            quantizations=sorted(by_precision),
            contestants=contestants,
            endpoint_count=len(endpoints),
            is_floating_alias=is_floating_alias(row.openrouter_id),
            credit_cost=row.credits,
        )


# ---------------------------------------------------------------------- players


class PlayerOut(Schema):
    id: uuid.UUID
    colour: Colour
    kind: PlayerKind
    display_name: str
    model: str | None = None
    persona: str | None = None

    #: What this seat ran on. `pinned_provider` is the endpoint chosen before the game started
    #: (ADR-0015); `providers_used` is what actually served it. **They should be the same single
    #: name** — a mismatch, or more than one entry, means the pin did not hold and the result
    #: measures a blend. That happened before pinning existed: one 80-ply game was served by two
    #: endpoints and its numbers cannot be reproduced.
    provider_routing: dict[str, Any] = Field(default_factory=dict)
    pinned_provider: str | None = None
    providers_used: list[str] = Field(default_factory=list)
    quantization: str | None = None

    @property
    def endpoint_held(self) -> bool:
        """False when more than one endpoint served this seat, or none matched the pin."""
        if not self.providers_used:
            return True
        if len(self.providers_used) > 1:
            return False
        return self.pinned_provider in (None, self.providers_used[0])

    illegal_attempts: int
    forfeited: bool
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    total_cost_usd: Decimal

    @classmethod
    def from_model(
        cls,
        row: Player,
        *,
        providers_used: list[str] | None = None,
        quantization: str | None = None,
    ) -> PlayerOut:
        model = (row.sampling or {}).get("model")
        only = (row.provider_routing or {}).get("only") or []
        return cls(
            provider_routing=row.provider_routing or {},
            pinned_provider=str(only[0]) if only else None,
            providers_used=providers_used or [],
            quantization=quantization,
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
    def from_model(
        cls,
        game: Game,
        players: list[Player],
        *,
        served_by: dict[uuid.UUID, tuple[list[str], str | None]] | None = None,
    ) -> GameSummary:
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
                (
                    PlayerOut.from_model(
                        p,
                        providers_used=(served_by or {}).get(p.id, ([], None))[0],
                        quantization=(served_by or {}).get(p.id, ([], None))[1],
                    )
                    for p in players
                ),
                key=lambda p: p.colour.value,
            ),
        )


class MyGameSummary(GameSummary):
    """A game the caller holds a seat in.

    The two extra fields are the caller's alone, which is why this is a separate shape rather than
    fields on `GameSummary`: putting "whose seat" on the public payload would publish who plays
    what to every spectator, the same reason `/games/{id}/seat` exists at all.
    """

    your_colour: Colour
    #: True when the game is running and it is this person's move — what a "your turn" list needs.
    your_turn: bool


class GameDetail(GameSummary):
    start_fen: str
    current_fen: str
    termination_detail: str | None
    prompt_version: str | None
    tool_schema_version: str | None
    max_usd: Decimal | None
    max_illegal_retries: int
    max_plies: int
    provider_routing: dict[str, Any] = Field(default_factory=dict)
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
        served_by: dict[uuid.UUID, tuple[list[str], str | None]] | None = None,
    ) -> GameDetail:
        summary = GameSummary.from_model(game, players, served_by=served_by)
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
            provider_routing=game.provider_routing or {},
            event_seq=game.event_seq,
            moves=moves,
        )


class CreateGameRequest(BaseModel):
    """Start a model-vs-model game.

    Requires an account: this is the only endpoint that spends money (AUTH-02), and it is behind
    the four budget layers of ADR-0011.
    """

    white: str = Field(description="OpenRouter model id for White")
    black: str = Field(description="OpenRouter model id for Black")

    white_quantization: str | None = Field(
        default=None,
        description=(
            "Precision for White. Part of the contestant's identity, not a filter — 'fp4' seats a "
            "different entrant from 'fp8' (ADR-0015). Omit to take the healthiest endpoint at "
            "whatever precision, which is then recorded."
        ),
    )
    black_quantization: str | None = Field(default=None, description="Precision for Black.")
    is_ranked: bool = False
    trash_talk_enabled: bool = True
    max_usd: Decimal | None = Field(default=Decimal("0.50"), ge=0)
    max_plies: int = Field(default=300, ge=2, le=1000)
    start_fen: str | None = None


# ---------------------------------------------------------------------- human play


class CreateHumanGameRequest(BaseModel):
    """Sit down against a model (HUMAN-01).

    Never ranked, and not offered as an option: a person is not a contestant, and a rating
    computed partly from human games would not measure what the leaderboard claims to.
    """

    model: str = Field(description="OpenRouter model id for the machine seat")
    model_quantization: str | None = Field(
        default=None,
        description="Precision for the model. Omit to take the healthiest endpoint (ADR-0015).",
    )
    colour: Colour = Field(default=Colour.WHITE, description="The colour *you* play.")
    trash_talk_enabled: bool = True
    max_usd: Decimal | None = Field(default=Decimal("0.50"), ge=0)
    max_plies: int = Field(default=300, ge=2, le=1000)


class HumanMoveRequest(BaseModel):
    move: str = Field(
        min_length=2,
        max_length=10,
        description="Algebraic (e4, Nf3, O-O, e8=Q) or UCI (e2e4, e7e8q).",
    )
    expected_ply: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The ply count your client believes the game is at. Supplied to make a resubmitted "
            "move harmless: if the server has moved on, the request is refused rather than "
            "playing a second move."
        ),
    )


class HumanSayRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class DrawResponseRequest(BaseModel):
    accept: bool


class HumanActionResponse(Schema):
    """The state of the game after a human action."""

    ply: int
    status: GameStatus
    result: GameResult
    termination: str | None = None
    detail: str = ""
    #: Present when the action ended the game, so the client need not refetch to know.
    game_over: bool = False


class SeatOut(Schema):
    """Which colour the caller plays here, or `null` for a spectator."""

    colour: Colour | None


class IllegalMoveResponse(Schema):
    """A refused move, with everything needed to try again (ADR-0002).

    A person gets the same courtesy a model does — the reason and the full legal move list — but
    no retry budget and no forfeit. Nobody loses a game to a mis-drag.
    """

    detail: str
    reason: str
    legal_moves: list[str]


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


class RawCallOut(Schema):
    """One LLM call, verbatim (LOG-01).

    `request` and `response` are the payloads exactly as they crossed the wire, minus redacted
    credentials. Nothing here is reshaped or summarised: the whole value of this endpoint is that
    a sceptical reader can check a cost or a token count against what the provider actually said.
    """

    id: int
    sequence: int
    model_slug: str
    provider: str | None
    request: dict[str, Any]
    response: dict[str, Any] | None
    reasoning_text: str | None
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    cost_usd: Decimal
    latency_ms: int | None
    finish_reason: str | None
    error: str | None
    created_at: dt.datetime

    @classmethod
    def from_model(cls, row: LlmCall) -> RawCallOut:
        return cls.model_validate(row)


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


# ---------------------------------------------------------------------- admin


class CreditGrantRequest(BaseModel):
    """How many credits to add. Negative takes them away (ADR-0016)."""

    credits: int = Field(description="Credits to add; negative removes them.")


class CreditGrantOut(Schema):
    user_id: uuid.UUID
    #: The balance after the grant.
    credit_balance: int
    #: What was just applied, echoed so an operator can see the change they made took effect.
    granted: int


class AdminSpend(Schema):
    """Today's spend against the kill switch, plus the recorded totals to check it against."""

    spent_today_usd: Decimal
    daily_limit_usd: Decimal
    remaining_usd: Decimal | None
    tripped: bool
    lifetime_recorded_usd: Decimal
    games_total: int
    games_running: int


class AdminUsage(Schema):
    user_id: uuid.UUID
    day: dt.date
    games_started: int
    usd_spent: Decimal


class MeOut(Schema):
    """Who the caller is, and what they have left today.

    The frontend needs the remaining allowance to say "3 of 20 games today" rather than letting
    someone discover the quota by being refused.
    """

    id: uuid.UUID
    email: str | None
    display_name: str | None
    is_admin: bool
    #: Credits held. Granted by an administrator and spent to start a game (ADR-0016).
    credit_balance: int

    #: Kept for the admin spend view; no longer a limit on anything.
    games_started_today: int
    usd_spent_today: Decimal


# ---------------------------------------------------------------------- leaderboard


class LeaderboardRow(Schema):
    """One contestant's standing (BENCH-02).

    `rd` is printed next to the rating on purpose. A rating without its deviation invites a reader
    to compare a model with three games against one with three hundred as though the numbers meant
    the same thing, and they do not — that is the whole reason Glicko-2 was chosen over Elo.
    """

    model_id: uuid.UUID
    model_slug: str
    quantization: str
    display_name: str

    rating: float
    rating_deviation: float
    volatility: float

    games: int
    wins: int
    draws: int
    losses: int

    #: The benchmark's headline number, and the reason the project exists.
    illegal_attempts: int
    moves_played: int
    illegal_per_move: float

    forfeits: int
    mean_cost_usd: Decimal
    mean_latency_ms: float

    @property
    def label(self) -> str:
        return f"{self.model_slug}@{self.quantization}"


class ExcludedGame(Schema):
    """A finished game that did not count, and why.

    Served rather than hidden. "Some games are excluded" invites disbelief; a list of ids and
    reasons is checkable (BENCH-10).
    """

    game_id: uuid.UUID
    reason: str


class Leaderboard(Schema):
    rows: list[LeaderboardRow] = Field(default_factory=list)
    games_counted: int = 0
    excluded: list[ExcludedGame] = Field(default_factory=list)
    prompt_version: str | None = None
    #: Rating periods that contributed. One per UTC day.
    periods: int = 0

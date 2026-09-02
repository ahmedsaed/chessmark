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
from chessmark.db.enums import CreditReason, EventType, GameStatus, PlayerKind, TurnStatus
from chessmark.db.models import (
    CreditLedger,
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
from chessmark.db.stats import ModelStats
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


class ModelStatsOut(Schema):
    """What a model has actually done, over every game (Phase 20, BENCH-02 extended).

    Not the leaderboard's numbers. Those cover ranked games only, keyed by contestant, because
    that is all a rating may see (BENCH-03). These cover exhibition and human games too, which is
    the difference between "how is it rated" and "what has it done".
    """

    games: int
    seats: int
    """Higher than `games` only when a model played itself — then it won one and lost one."""

    wins: int
    draws: int
    losses: int
    forfeits: int

    illegal_attempts: int
    moves_played: int
    illegal_per_move: float

    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    cache_rate: float | None = None
    total_cost_usd: Decimal
    cost_per_game: Decimal
    mean_latency_ms: float | None = None

    @classmethod
    def from_stats(cls, stats: ModelStats) -> ModelStatsOut:
        return cls(
            games=stats.games,
            seats=stats.seats,
            wins=stats.wins,
            draws=stats.draws,
            losses=stats.losses,
            forfeits=stats.forfeits,
            illegal_attempts=stats.illegal_attempts,
            moves_played=stats.moves_played,
            illegal_per_move=stats.illegal_per_move,
            llm_calls=stats.llm_calls,
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            total_tokens=stats.total_tokens,
            cached_tokens=stats.cached_tokens,
            cache_rate=stats.cache_rate,
            total_cost_usd=stats.total_cost_usd,
            cost_per_game=stats.cost_per_game,
            mean_latency_ms=stats.mean_latency_ms,
        )


class ModelDetail(ModelOut):
    """One model, with everything we know about how it has played."""

    stats: ModelStatsOut

    #: Ratings this model's contestants hold, when any of them are ranked. Empty is a fact worth
    #: showing — a floating alias can never be ranked, and a new model has simply not played yet.
    ratings: list[LeaderboardRow] = Field(default_factory=list)


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
    #: How many times this seat summarised its own history to stay inside its window (ADR-0018).
    compactions: int = 0
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
            compactions=row.compactions,
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
    #: Set only while `status` is `paused`. One short line and a time, because a board that has
    #: stopped moving needs to say why on the card, not only on the game page.
    pause_reason: str | None = None
    resume_after: dt.datetime | None = None
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
            pause_reason=game.pause_reason,
            resume_after=game.resume_after,
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
    """Who to grant to, and how many. Negative takes them away (ADR-0016)."""

    user: str = Field(
        description=(
            "An email address, a Clerk user id, or a Chessmark user id — whichever you have. "
            "An email Chessmark does not know is looked up with Clerk, so credits can be granted "
            "to someone who has not signed in yet."
        )
    )
    credits: int = Field(description="Credits to add; negative removes them.")
    note: str | None = Field(
        default=None,
        description="Why. Recorded on the ledger row, because a reason code cannot carry it.",
    )


class CreditGrantOut(Schema):
    user_id: uuid.UUID
    #: Echoed so an operator can see *who* they just granted to, not only that it worked.
    email: str | None = None
    #: The balance after the grant.
    credit_balance: int
    #: What was just applied, echoed so an operator can see the change they made took effect.
    granted: int


class CreditEntryOut(Schema):
    """One movement of a balance (AUTH-13)."""

    id: int
    delta: int
    balance_after: int
    reason: CreditReason
    game_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    note: str | None = None
    created_at: dt.datetime

    @classmethod
    def from_model(cls, row: CreditLedger) -> CreditEntryOut:
        return cls(
            id=row.id,
            delta=row.delta,
            balance_after=row.balance_after,
            reason=CreditReason(row.reason),
            game_id=row.game_id,
            actor_user_id=row.actor_user_id,
            note=row.note,
            created_at=row.created_at,
        )


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
    #: `rating_deviation` said in a word. Most readers do not know what to do with "± 208";
    #: "provisional" is the same fact in a form they can act on.
    provisional: bool
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

    @classmethod
    def from_rating(
        cls,
        contestant: Any,
        rating: Any,
        aggregate: Any,
        *,
        display_name: str | None = None,
    ) -> LeaderboardRow:
        """One row, built in one place.

        The leaderboard and a model page print the same numbers, and building them separately is
        how two views of one rating start disagreeing. A contestant with no aggregate is a rating
        with no ratable games behind it — every count is zero rather than absent, because the row
        still exists.
        """
        return cls(
            model_id=contestant.model_id,
            model_slug=contestant.model_slug,
            quantization=contestant.quantization,
            display_name=display_name or contestant.model_slug,
            rating=rating.rating,
            rating_deviation=rating.rd,
            volatility=rating.volatility,
            provisional=rating.provisional,
            games=aggregate.games if aggregate else 0,
            wins=aggregate.wins if aggregate else 0,
            draws=aggregate.draws if aggregate else 0,
            losses=aggregate.losses if aggregate else 0,
            illegal_attempts=aggregate.illegal_attempts if aggregate else 0,
            moves_played=aggregate.moves_played if aggregate else 0,
            illegal_per_move=aggregate.illegal_per_move if aggregate else 0.0,
            forfeits=aggregate.forfeits if aggregate else 0,
            mean_cost_usd=aggregate.mean_cost_usd if aggregate else Decimal(0),
            mean_latency_ms=aggregate.mean_latency_ms if aggregate else 0.0,
        )


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


# ---------------------------------------------------------------------- tournaments


class StandingOut(Schema):
    """One row of the table."""

    place: int
    key: str
    display_name: str
    seed: int
    played: int
    wins: int
    draws: int
    losses: int
    byes: int
    score: float
    sonneborn_berger: float
    #: Glicko-2 over this event's games alone, and `None` for a closed event.
    #:
    #: A pool has no fixed schedule, so its entrants play unequal numbers of games and a sum of
    #: points ranks partly by volume — `score` and `sonneborn_berger` stay in the payload because
    #: they are facts worth reading, but for a pool they do not decide the order (ADR-0027).
    #: `None` on a rated event means the model has not yet completed a game that counts.
    rating: float | None = None
    rating_deviation: float | None = None
    #: Whether that rating is still too unsure to read as a placing. `False` when there is no
    #: rating at all — an entrant with no ratable game is *unrated*, which the table says outright,
    #: rather than a provisional one.
    rating_provisional: bool = False


class TournamentPairingOut(Schema):
    """One scheduled pairing, in whatever state it is in.

    The state is derived rather than stored: a pairing with a score is `played`, one whose game is
    paused is `paused`, one with a running game is `live`, one with neither is `waiting`. That keeps
    the page honest about what is actually happening rather than about what a scheduler last wrote
    down — "live" once covered a game sitting on a provider cooldown.
    """

    id: uuid.UUID
    round_number: int
    white_key: str
    black_key: str | None
    white_score: float | None
    state: str
    game_id: uuid.UUID | None
    abandoned_reason: str | None
    started_at: dt.datetime | None
    ended_at: dt.datetime | None


class TournamentStats(Schema):
    """What the event has cost and produced so far.

    Money and tokens come from `llm_calls` by way of the games, so a tournament page cannot print
    a figure the call log disagrees with (invariant 4).
    """

    #: Every pairing written down — the sum of the four states below, not a state itself.
    pairings: int
    played: int
    live: int
    #: Holding a game that is not moving — a provider cooldown (ADR-0017). Counted apart from
    #: `live`, which used to include them and made a stalled event look busy.
    paused: int = 0
    #: Written down, not yet started. Deliberately not "queued": nothing is in the job queue for
    #: these, and only the live game has one. What holds them back is the concurrency bound.
    waiting: int
    abandoned: int
    total_cost_usd: Decimal
    total_tokens: int
    total_plies: int
    mean_plies: float | None
    illegal_attempts: int
    decisive: int
    draws: int


class TournamentSummary(Schema):
    id: uuid.UUID
    slug: str
    name: str
    status: str
    format: str
    double: bool
    rounds: int
    is_ranked: bool
    max_concurrent: int
    max_usd: Decimal | None
    entrant_count: int
    field_description: str
    created_at: dt.datetime
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    stats: TournamentStats


class TournamentDetail(TournamentSummary):
    standings: list[StandingOut] = Field(default_factory=list)
    pairings: list[TournamentPairingOut] = Field(default_factory=list)
    games: list[GameSummary] = Field(default_factory=list)

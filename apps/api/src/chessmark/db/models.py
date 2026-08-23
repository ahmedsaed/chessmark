"""The Chessmark schema.

Everything is stored: every ply, every turn, every LLM request and response verbatim, every tool
call, every taunt, and an ordered event log that drives live streaming, reconnect, and replay
alike (ADR-0008).

Two conventions worth knowing before reading:

* **Identity.** Anything with a public identity (users, games, players, models) gets a UUID, so
  URLs are not enumerable. Append-only log rows get a bigserial, because nothing outside the
  system ever addresses them.
* **Foreign keys are indexed.** Postgres does not do this for you, and a benchmark that spends its
  life joining plies to games would feel it. `tests/db/test_schema.py` enforces it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chessmark.db.base import (
    USD,
    USD_PER_TOKEN,
    Base,
    bigint_pk,
    created_at,
    enum_column,
    updated_at,
    uuid_pk,
)
from chessmark.db.enums import (
    AnalysisStatus,
    EventType,
    GameStatus,
    ModerationStatus,
    PlayerKind,
    TurnStatus,
)
from chessmark.game import Colour, GameResult, Termination


def _fk(target: str, **kwargs: Any) -> sa.ForeignKey:
    return sa.ForeignKey(target, **kwargs)


# ============================================================================ identity


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    clerk_user_id: Mapped[str] = mapped_column(sa.Text, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(sa.Text)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    created_at: Mapped[dt.datetime] = created_at()
    updated_at: Mapped[dt.datetime] = updated_at()


class ModelRegistry(Base):
    """Playable models and their pricing.

    Pricing is load-bearing: it feeds the budget caps in ADR-0011, so stale numbers mean wrong
    caps. Refreshed from OpenRouter by `scripts/refresh_model_seed.py`.
    """

    __tablename__ = "model_registry"

    id: Mapped[uuid.UUID] = uuid_pk()
    openrouter_id: Mapped[str] = mapped_column(sa.Text, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(sa.Text)
    provider: Mapped[str] = mapped_column(sa.Text, index=True)
    context_length: Mapped[int | None] = mapped_column(sa.Integer)
    prompt_usd_per_token: Mapped[Decimal] = mapped_column(USD_PER_TOKEN, default=Decimal(0))
    completion_usd_per_token: Mapped[Decimal] = mapped_column(USD_PER_TOKEN, default=Decimal(0))
    supports_reasoning: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    supports_tools: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    is_free: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    created_at: Mapped[dt.datetime] = created_at()
    updated_at: Mapped[dt.datetime] = updated_at()


# ============================================================================ the game


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = uuid_pk()
    status: Mapped[GameStatus] = mapped_column(
        enum_column(GameStatus), default=GameStatus.PENDING, index=True
    )

    start_fen: Mapped[str] = mapped_column(sa.Text)
    result: Mapped[GameResult] = mapped_column(enum_column(GameResult), default=GameResult.ONGOING)
    termination: Mapped[Termination | None] = mapped_column(enum_column(Termination))
    termination_detail: Mapped[str | None] = mapped_column(sa.Text)
    winner_colour: Mapped[Colour | None] = mapped_column(enum_column(Colour))
    ply_count: Mapped[int] = mapped_column(default=0, server_default="0")

    #: Monotonic counter backing `game_events.seq`. Incremented under a row lock so appends are
    #: gap-free even with several workers writing at once (ADR-0008).
    event_seq: Mapped[int] = mapped_column(default=0, server_default="0")

    # --- benchmark configuration: recorded so ranked runs stay comparable (BENCH-04) ---
    is_ranked: Mapped[bool] = mapped_column(default=False, server_default=sa.false(), index=True)
    trash_talk_enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    max_illegal_retries: Mapped[int] = mapped_column(default=5, server_default="5")
    max_plies: Mapped[int] = mapped_column(default=300, server_default="300")

    #: GAME-09. Both default on; the referee will honour these in a later phase.
    auto_threefold_draw: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    auto_fifty_move_draw: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    prompt_version: Mapped[str | None] = mapped_column(sa.Text)
    tool_schema_version: Mapped[str | None] = mapped_column(sa.Text)

    #: The OpenRouter provider-routing policy this game ran under — which quantizations it would
    #: accept, the sort, any throughput floor. Recorded because it is as much a part of a result as
    #: the prompt version: the same model served at fp8 and at fp4 is not the same contestant, and
    #: a leaderboard that mixes them silently measures the routing lottery (BENCH-04).
    provider_routing: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )

    # --- accounting ---
    max_usd: Mapped[Decimal | None] = mapped_column(USD)
    total_cost_usd: Mapped[Decimal] = mapped_column(USD, default=Decimal(0), server_default="0")
    total_tokens: Mapped[int] = mapped_column(default=0, server_default="0")

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[dt.datetime] = created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column()
    ended_at: Mapped[dt.datetime | None] = mapped_column()

    players: Mapped[list[Player]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )
    plies: Mapped[list[Ply]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="Ply.ply_number"
    )

    __table_args__ = (sa.Index("ix_games_status_created_at", "status", "created_at"),)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = uuid_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    colour: Mapped[Colour] = mapped_column(enum_column(Colour))
    kind: Mapped[PlayerKind] = mapped_column(enum_column(PlayerKind))

    model_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("model_registry.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("users.id", ondelete="SET NULL"), index=True
    )

    #: Denormalised so a PGN or leaderboard row stays truthful even if the model is later renamed
    #: or removed from the registry.
    display_name: Mapped[str] = mapped_column(sa.Text)

    persona: Mapped[str | None] = mapped_column(sa.Text)
    system_prompt_version: Mapped[str | None] = mapped_column(sa.Text)
    sampling: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    #: The routing policy resolved for *this seat's* model. Per player, not per game: `only` names
    #: providers, and providers are model-specific, so one game-wide list cannot serve two vendors.
    #: Pinning Gemini to Google's endpoints and applying that same list to DeepSeek asks Google to
    #: serve a DeepSeek model, which is a 404. `games.provider_routing` records what was requested;
    #: this records what each model actually ran under.
    provider_routing: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )

    #: Monotonic counter backing `transcript_messages.seq`, allocated under a row lock exactly as
    #: `games.event_seq` is. Each player has its own independent transcript.
    transcript_seq: Mapped[int] = mapped_column(default=0, server_default="0")

    # --- per-player benchmark metrics ---
    illegal_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    forfeited: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    prompt_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cached_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    total_cost_usd: Mapped[Decimal] = mapped_column(USD, default=Decimal(0), server_default="0")

    created_at: Mapped[dt.datetime] = created_at()

    game: Mapped[Game] = relationship(back_populates="players")

    __table_args__ = (sa.UniqueConstraint("game_id", "colour", name="uq_players_game_id_colour"),)


class Turn(Base):
    """One agent turn. May span many LLM calls and many tool calls."""

    __tablename__ = "turns"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[uuid.UUID] = mapped_column(_fk("players.id", ondelete="CASCADE"), index=True)

    #: The ply this turn produced. Null when the turn forfeited without playing.
    ply_number: Mapped[int | None] = mapped_column(sa.Integer)
    status: Mapped[TurnStatus] = mapped_column(enum_column(TurnStatus), default=TurnStatus.RUNNING)

    illegal_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    tool_call_count: Mapped[int] = mapped_column(default=0, server_default="0")
    llm_call_count: Mapped[int] = mapped_column(default=0, server_default="0")

    prompt_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cached_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(USD, default=Decimal(0), server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)

    error: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[dt.datetime] = created_at()
    ended_at: Mapped[dt.datetime | None] = mapped_column()


class Ply(Base):
    """One committed half-move. Immutable once written."""

    __tablename__ = "plies"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, _fk("turns.id", ondelete="SET NULL"), index=True
    )

    ply_number: Mapped[int] = mapped_column(sa.Integer)
    colour: Mapped[Colour] = mapped_column(enum_column(Colour))
    san: Mapped[str] = mapped_column(sa.Text)
    uci: Mapped[str] = mapped_column(sa.Text)
    fen_before: Mapped[str] = mapped_column(sa.Text)
    fen_after: Mapped[str] = mapped_column(sa.Text)

    is_capture: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    is_check: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    is_checkmate: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    is_castling: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    is_en_passant: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    promotion: Mapped[str | None] = mapped_column(sa.String(1))

    think_ms: Mapped[int | None] = mapped_column(sa.Integer)

    # --- engine analysis: unused until Phase 14, present from the first migration (BENCH-08) ---
    eval_before_cp: Mapped[int | None] = mapped_column(sa.Integer)
    eval_after_cp: Mapped[int | None] = mapped_column(sa.Integer)
    cp_loss: Mapped[int | None] = mapped_column(sa.Integer)
    classification: Mapped[str | None] = mapped_column(sa.Text)
    mate_in: Mapped[int | None] = mapped_column(sa.Integer)
    engine_version: Mapped[str | None] = mapped_column(sa.Text)
    engine_depth: Mapped[int | None] = mapped_column(sa.Integer)
    analysed_at: Mapped[dt.datetime | None] = mapped_column()

    created_at: Mapped[dt.datetime] = created_at()

    game: Mapped[Game] = relationship(back_populates="plies")

    __table_args__ = (
        sa.UniqueConstraint("game_id", "ply_number", name="uq_plies_game_id_ply_number"),
    )


# ============================================================================ verbatim logs


class LlmCall(Base):
    """One provider round-trip, stored verbatim (LOG-01).

    `request` and `response` are the real payloads with credentials redacted — not a summary. If
    a number appears on the leaderboard, this row is what proves it.
    """

    __tablename__ = "llm_calls"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[int] = mapped_column(
        sa.BigInteger, _fk("turns.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(sa.Integer)

    model_slug: Mapped[str] = mapped_column(sa.Text, index=True)
    provider: Mapped[str | None] = mapped_column(sa.Text)

    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reasoning_text: Mapped[str | None] = mapped_column(sa.Text)

    prompt_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cached_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(USD, default=Decimal(0), server_default="0")

    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    finish_reason: Mapped[str | None] = mapped_column(sa.Text)
    error: Mapped[str | None] = mapped_column(sa.Text)

    #: Object-storage key when the payload was offloaded (LOG-05). When set, `request`/`response`
    #: hold a truncated form and this is the authority.
    payload_ref: Mapped[str | None] = mapped_column(sa.Text)

    created_at: Mapped[dt.datetime] = created_at()

    __table_args__ = (
        sa.UniqueConstraint("turn_id", "sequence", name="uq_llm_calls_turn_id_sequence"),
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[int] = mapped_column(
        sa.BigInteger, _fk("turns.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(sa.Integer)

    name: Mapped[str] = mapped_column(sa.Text, index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ok: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    error: Mapped[str | None] = mapped_column(sa.Text)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[dt.datetime] = created_at()

    __table_args__ = (
        sa.UniqueConstraint("turn_id", "sequence", name="uq_tool_calls_turn_id_sequence"),
    )


class TranscriptMessage(Base):
    """One message in a player's conversation with the model. Append-only, never rewritten.

    This table *is* the mechanism behind invariant 2 (ADR-0003). The transcript is rebuilt from
    these rows at the start of every turn, so a byte-identical cacheable prefix is not something
    the turn loop has to remember to preserve — it is a property of rows being immutable.

    It duplicates content that also appears in `llm_calls` and `tool_calls`. That is deliberate:
    those are the *record* of what happened, this is the *input* we replay, and reconstructing one
    from the other would put an exact-serialisation requirement on the hot path.
    """

    __tablename__ = "transcript_messages"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[uuid.UUID] = mapped_column(_fk("players.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, _fk("turns.id", ondelete="SET NULL"), index=True
    )

    seq: Mapped[int] = mapped_column(sa.Integer)
    role: Mapped[str] = mapped_column(sa.Text)
    """system | user | assistant | tool"""

    content: Mapped[str | None] = mapped_column(sa.Text)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    """Present on assistant messages that requested tools."""

    reasoning_details: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    """OpenRouter's normalised reasoning blocks, replayed verbatim on later turns.

    Not decoration. Gemini 3 refuses a function call whose `thought_signature` is missing, and
    DeepSeek refuses a thinking-mode history without `reasoning_content`; both arrive here. Storing
    them is what makes the replayed transcript a history those models will accept.
    """

    tool_call_id: Mapped[str | None] = mapped_column(sa.Text)
    name: Mapped[str | None] = mapped_column(sa.Text)
    """Tool name, on tool-result messages."""

    created_at: Mapped[dt.datetime] = created_at()

    __table_args__ = (
        sa.UniqueConstraint("player_id", "seq", name="uq_transcript_messages_player_id_seq"),
        sa.Index("ix_transcript_messages_player_id_seq", "player_id", "seq"),
    )


class Message(Base):
    """Trash talk. Blocked messages are stored and flagged, never dropped (TALK-05)."""

    __tablename__ = "messages"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("players.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, _fk("turns.id", ondelete="SET NULL"), index=True
    )
    ply_number: Mapped[int | None] = mapped_column(sa.Integer)

    content: Mapped[str] = mapped_column(sa.Text)
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        enum_column(ModerationStatus), default=ModerationStatus.PENDING, index=True
    )
    moderation_detail: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime] = created_at()


class GameEvent(Base):
    """The ordered log behind live streaming, reconnect backfill, and replay (ADR-0008).

    `seq` is allocated from `games.event_seq` under a row lock, so it is monotonic and gap-free
    even when several workers append at once. The unique constraint is the backstop.
    """

    __tablename__ = "game_events"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(_fk("games.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(sa.Integer)
    type: Mapped[EventType] = mapped_column(enum_column(EventType))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[dt.datetime] = created_at()

    __table_args__ = (
        sa.UniqueConstraint("game_id", "seq", name="uq_game_events_game_id_seq"),
        sa.Index("ix_game_events_game_id_seq", "game_id", "seq"),
    )


# ============================================================================ benchmark


class ModelEndpoint(Base):
    """One provider serving one model, and at what precision.

    OpenRouter fans a single model id out across many providers — `deepseek-v4-flash` has 18,
    quantized anywhere from fp8 down to fp4, some declaring nothing at all. The chat response names
    the provider but never its precision, so this is the table that turns "AtlasCloud" into "fp8"
    and lets a finished game say what it was actually played at.

    Nothing is deleted on refresh, only deactivated: a game that already ran must stay explicable
    after a provider disappears.
    """

    __tablename__ = "model_endpoints"

    id: Mapped[int] = bigint_pk()
    model_id: Mapped[uuid.UUID] = mapped_column(
        _fk("model_registry.id", ondelete="CASCADE"), index=True
    )
    provider_name: Mapped[str] = mapped_column(sa.Text, index=True)
    quantization: Mapped[str | None] = mapped_column(sa.Text)
    context_length: Mapped[int | None] = mapped_column(sa.Integer)
    supports_tools: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    max_completion_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    #: Health, as OpenRouter measured it when this row was last refreshed. Endpoint selection is
    #: by uptime (ADR-0015), so these are load-bearing rather than informational — and they are
    #: stored rather than fetched live so a game can always say what the numbers were when its
    #: endpoint was chosen.
    uptime_30m: Mapped[float | None] = mapped_column(sa.Float)
    uptime_1d: Mapped[float | None] = mapped_column(sa.Float)
    throughput: Mapped[float | None] = mapped_column(sa.Float)
    latency_ms: Mapped[float | None] = mapped_column(sa.Float)

    #: Whether this endpoint caches without being asked. Anthropic and Alibaba do not, which is
    #: why `agents/caching.py` exists; recording it per endpoint makes a 0% hit rate explicable
    #: instead of suspicious.
    supports_implicit_caching: Mapped[bool | None] = mapped_column(sa.Boolean)

    refreshed_at: Mapped[dt.datetime] = updated_at()

    __table_args__ = (
        sa.UniqueConstraint(
            "model_id", "provider_name", name="uq_model_endpoints_model_id_provider_name"
        ),
    )


class Rating(Base):
    """Glicko-2 rating for one **contestant** at the end of one rating period (BENCH-01).

    A contestant is `(model, quantization)`, not a model (ADR-0015). `model@fp4` and `model@fp8`
    are different entrants and are rated apart — averaging them would produce a number describing
    neither, which is the failure this project keeps finding in its own results.
    """

    __tablename__ = "ratings"

    id: Mapped[int] = bigint_pk()
    model_id: Mapped[uuid.UUID] = mapped_column(
        _fk("model_registry.id", ondelete="CASCADE"), index=True
    )
    quantization: Mapped[str] = mapped_column(
        sa.Text, default="unknown", server_default="unknown", index=True
    )
    """The precision this contestant played at. Half of its identity, not a detail."""

    period: Mapped[int] = mapped_column(sa.Integer, index=True)
    rating: Mapped[float] = mapped_column(sa.Float, default=1500.0)
    rating_deviation: Mapped[float] = mapped_column(sa.Float, default=350.0)
    volatility: Mapped[float] = mapped_column(sa.Float, default=0.06)
    games_played: Mapped[int] = mapped_column(default=0, server_default="0")
    computed_at: Mapped[dt.datetime] = created_at()

    __table_args__ = (
        sa.UniqueConstraint(
            "model_id", "quantization", "period", name="uq_ratings_contestant_period"
        ),
    )


class AnalysisJob(Base):
    """Queued Stockfish annotation for a finished game (BENCH-06). Never in the live path."""

    __tablename__ = "analysis_jobs"

    id: Mapped[int] = bigint_pk()
    game_id: Mapped[uuid.UUID] = mapped_column(
        _fk("games.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        enum_column(AnalysisStatus), default=AnalysisStatus.PENDING, index=True
    )
    engine: Mapped[str | None] = mapped_column(sa.Text)
    engine_version: Mapped[str | None] = mapped_column(sa.Text)
    depth: Mapped[int | None] = mapped_column(sa.Integer)
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[dt.datetime] = created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column()
    completed_at: Mapped[dt.datetime | None] = mapped_column()


class UsageLedger(Base):
    """Per-user, per-day quota accounting — layer 2 of the budget controls (ADR-0011)."""

    __tablename__ = "usage_ledger"

    id: Mapped[int] = bigint_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(_fk("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[dt.date] = mapped_column(sa.Date, index=True)
    games_started: Mapped[int] = mapped_column(default=0, server_default="0")
    usd_spent: Mapped[Decimal] = mapped_column(USD, default=Decimal(0), server_default="0")
    updated_at: Mapped[dt.datetime] = updated_at()

    __table_args__ = (sa.UniqueConstraint("user_id", "day", name="uq_usage_ledger_user_id_day"),)

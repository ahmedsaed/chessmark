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
    CreditReason,
    EventType,
    GameStatus,
    ModerationStatus,
    PlayerKind,
    TournamentStatus,
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

    #: Credits held, spent to start a game (ADR-0016). **Zero by default and granted by an
    #: administrator** — it does not regenerate, so a new account cannot play until someone says
    #: so. Deliberate for the testing phase; a signup grant changes this default and nothing else.
    credit_balance: Mapped[int] = mapped_column(default=0, server_default="0")

    created_at: Mapped[dt.datetime] = created_at()
    updated_at: Mapped[dt.datetime] = updated_at()


class ModelRegistry(Base):
    """Playable models and their pricing.

    Pricing is load-bearing: it feeds the budget caps in ADR-0011, so stale numbers mean wrong
    caps. Refreshed from OpenRouter by `make seed-models`.
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

    #: The model's HuggingFace repository, when OpenRouter names one.
    #:
    #: The closest thing to a fact about open weights that the catalogue actually carries — 169 of
    #: 418 models declare it. A published repository is strong evidence the weights are open; its
    #: absence is weaker evidence they are closed, since a vendor may simply not have linked one.
    #: Stored rather than curated so an "open weights against closed" bracket is a query rather
    #: than a hand-maintained list that rots.
    hugging_face_id: Mapped[str | None] = mapped_column(sa.Text)

    #: What a seat against this model costs to start, in credits (ADR-0016). **Derived** from the
    #: model's own prices at catalogue sync, so it is rewritten on every `make seed-models`.
    credit_cost: Mapped[int] = mapped_column(default=1, server_default="1")

    #: An administrator's price for this model, which wins over the derived one. Separate column
    #: precisely so re-seeding cannot silently undo a deliberate exception.
    credit_cost_override: Mapped[int | None] = mapped_column(sa.Integer)

    @property
    def credits(self) -> int:
        """What this model actually costs. The override if there is one, else the derived tier.

        A `:free` model still has a price here, and that is deliberate: credits are what stops an
        unfunded account starting games at all (AUTH-11), and pricing free models at zero opened
        `POST /games` to anyone signed in. Where free genuinely means free is a person playing one
        themselves — see `routes/games.py`, which is the only place that exempts it.
        """
        return (
            self.credit_cost_override if self.credit_cost_override is not None else self.credit_cost
        )

    created_at: Mapped[dt.datetime] = created_at()
    updated_at: Mapped[dt.datetime] = updated_at()


# ============================================================================ the game


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = uuid_pk()
    status: Mapped[GameStatus] = mapped_column(
        enum_column(GameStatus), default=GameStatus.PENDING, index=True
    )

    #: When a `PAUSED` game may run again, and why it stopped. Absolute time rather than a
    #: duration, so the resumer only ever asks "is it time yet" — see `core/cooldown.py`.
    resume_after: Mapped[dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: One short line, shown on the page. The provider's raw refusal is not it: that is a JSON
    #: blob carrying an account id, and a reader wants "rate-limited upstream by Google AI Studio".
    pause_reason: Mapped[str | None] = mapped_column(sa.Text)

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
    #: Apply a *claimable* draw rule without waiting for a claim. **Off**, and it used to be both
    #: on and unread: the referee auto-drew regardless of what these said.
    #:
    #: FIDE makes threefold and the fifty-move rule a claim by the player having the move (9.2,
    #: 9.3) because a repetition usually favours one side — deciding for both is not neutral. The
    #: hard backstops (fivefold, seventy-five moves) are not switchable and always apply, so a game
    #: still cannot loop for ever. See ADR-0020.
    auto_threefold_draw: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    auto_fifty_move_draw: Mapped[bool] = mapped_column(default=False, server_default=sa.false())

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

    #: The last `usage.prompt_tokens` the provider reported for this seat, carried between turns.
    #:
    #: **Why it is stored rather than held in memory.** The worker builds a fresh `TurnRunner` for
    #: every turn, so a counter living on the runner is zero at the start of each one — which meant
    #: the first call of *every* turn fell back to a character estimate, for the whole game, when
    #: the design said the estimate ran once. It said 477,155 tokens of a six-ply transcript and
    #: forfeited a model (ADR-0021).
    #:
    #: Zero means "nothing measured yet", which is true only before a game's first response.
    #: Invariant 4 already says money comes from returned token counts; this applies the same rule
    #: to the arithmetic deciding whether a request can be sent at all (AGENT-19).
    last_prompt_tokens: Mapped[int] = mapped_column(default=0, server_default="0")

    # --- per-player benchmark metrics ---
    illegal_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    compactions: Mapped[int] = mapped_column(default=0, server_default="0")
    """How many times this seat summarised its own history (ADR-0018).

    Worth showing beside the illegal attempts and the token counts, because it says something about
    the model: a seat that compacted four times was verbose enough to fill its window four times in
    one game, and a reader comparing two models wants that visible rather than buried in the event
    log.
    """
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

    superseded_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    """Set when a compaction folded this message into a summary (ADR-0018).

    **The row is never deleted or rewritten.** This table is the record of what we replayed, and
    invariant 3 asks that the record be verbatim — so a compacted game keeps every message it ever
    sent and `build_messages` simply stops sending the folded ones. What changes is the request,
    not the history.
    """

    trimmed_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    """Set when a compaction elided this message's content but kept the message (ADR-0021).

    The cheap rung of the ladder, and the reason it is a second mark rather than a `superseded_at`:
    a `tool` result cannot simply stop being sent, because the assistant message that requested it
    would then carry a `tool_call_id` with no answer and every provider refuses that. So the
    message stays, its content is replaced *in the request* by `compaction.TRIMMED_PLACEHOLDER`,
    and the row itself is untouched — the column records the decision, `content` still holds what
    the tool actually returned.

    Only stale tool output is trimmed. It is the bulk of a chess transcript — `get_legal_moves`
    returns 38 or 39 move objects and a turn calls it most plies — and it is worth nothing once the
    position has moved on, because the board is authoritative (invariant 1) and the model can ask
    again.
    """

    is_summary: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    """This row *is* a compaction summary, written by the model about its own earlier turns.

    Flagged rather than inferred, because it decides *ordering*: `seq` is append-only, so a summary
    written at ply 60 has the highest sequence number in the table and would replay after the turns
    it summarises. The builder puts the system prompt first, then the live summary, then the
    retained turns.
    """

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


class CreditLedger(Base):
    """Every movement of a credit balance, append-only (AUTH-13, ADR-0016).

    `users.credit_balance` stays the enforcement point — the charge has to be one statement whose
    `WHERE` clause is the check, and a balance summed from history on every request could not do
    that. This is the *account* of how it got there, and the two are asserted to agree.

    Append-only in the same sense the game event log is: a revocation is a negative row, never an
    edit, so a balance's history cannot be rewritten to hide a mistake. Rows outlive the thing they
    reference — `game_id` is `SET NULL`, because a deleted game must not erase the record that
    somebody paid for it.

    Deliberately shaped so a future top-up writes here too. Why a balance moved should not depend
    on who moved it, and `reason` already distinguishes a grant from a refund.
    """

    __tablename__ = "credit_ledger"

    id: Mapped[int] = bigint_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(_fk("users.id", ondelete="CASCADE"), index=True)

    #: Signed. Negative spends, positive grants — so the balance is the plain sum of the column.
    delta: Mapped[int] = mapped_column(sa.Integer)

    #: The balance immediately after this row, recorded rather than derived. It makes a divergence
    #: between the ledger and `users.credit_balance` visible at the row that caused it, instead of
    #: only in the total.
    balance_after: Mapped[int] = mapped_column(sa.Integer)

    reason: Mapped[CreditReason] = mapped_column(enum_column(CreditReason), index=True)

    #: The game this paid for, when the reason is a charge.
    game_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("games.id", ondelete="SET NULL"), index=True
    )

    #: The administrator who did it, when a person did. Null for a charge, which nobody decides.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("users.id", ondelete="SET NULL"), index=True
    )

    #: Free text from whoever granted. The "why" a reason code cannot carry.
    note: Mapped[str | None] = mapped_column(sa.Text)

    created_at: Mapped[dt.datetime] = created_at()


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


class Tournament(Base):
    """An automated event: a format, a field, and a set of bounds (BENCH-05).

    The configuration is stored rather than derived so a standings page can say what it selected
    and a finished event stays explicable after the registry has moved on — the same reason a game
    records its prompt version and routing policy (BENCH-04). `field_filter` is the description of
    who was invited; `tournament_entrants` is who actually turned up.
    """

    __tablename__ = "tournaments"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text)
    slug: Mapped[str] = mapped_column(sa.Text, unique=True, index=True)
    status: Mapped[TournamentStatus] = mapped_column(
        enum_column(TournamentStatus), default=TournamentStatus.PENDING, index=True
    )

    #: `round_robin` or `swiss`, and the knobs each needs.
    format: Mapped[str] = mapped_column(sa.Text)
    double: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    rounds: Mapped[int] = mapped_column(default=5, server_default="5")

    #: Who was invited (`tournament.FieldFilter`), stored verbatim so the selection can be
    #: explained and replayed even after the catalogue changes underneath it.
    field_filter: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    # --- bounds ---
    #: How many games may be in flight. One is not a placeholder: free models come from a shared
    #: pool that rate-limits, and the daily free allowance is consumed at about the rate a single
    #: game generates it.
    max_concurrent: Mapped[int] = mapped_column(default=1, server_default="1")
    #: The event's own ceiling, independent of any user's quota — this is the harness spending on
    #: its own initiative rather than a person spending theirs (ADR-0011).
    max_usd: Mapped[Decimal | None] = mapped_column(USD)
    max_plies_per_game: Mapped[int] = mapped_column(default=300, server_default="300")
    max_usd_per_game: Mapped[Decimal | None] = mapped_column(USD)
    is_ranked: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    #: The hours, in UTC, during which this event may start games. Null for either means always.
    #:
    #: Not a rate limiter — the free-tier counter is that. This is about *when* games happen: a
    #: site whose whole appeal is watching models play should have something to watch at the hours
    #: people are awake, rather than spending its daily allowance overnight. A window that wraps
    #: midnight (22:00 to 04:00) is supported and is why this is compared rather than subtracted.
    active_from: Mapped[dt.time | None] = mapped_column(sa.Time)
    active_until: Mapped[dt.time | None] = mapped_column(sa.Time)

    #: Games this event may start per UTC day. Null for no limit. Distinct from the account-wide
    #: free-tier allowance: this is how an operator divides that allowance between events.
    max_games_per_day: Mapped[int | None] = mapped_column(sa.Integer)

    total_cost_usd: Mapped[Decimal] = mapped_column(USD, default=Decimal(0), server_default="0")

    created_at: Mapped[dt.datetime] = created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column()
    ended_at: Mapped[dt.datetime | None] = mapped_column()

    entrants: Mapped[list[TournamentEntrant]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )
    games: Mapped[list[TournamentGame]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )


class TournamentEntrant(Base):
    """One contestant in the field.

    `key` is the contestant identity the pure module pairs on — `(model, quantization)` rendered
    as a string (ADR-0015), so the same weights served at two precisions enter separately, exactly
    as they are rated separately.

    `model_id` may be null if a model is later removed from the registry; the entrant row stays so
    the games it played remain explicable.
    """

    __tablename__ = "tournament_entrants"

    id: Mapped[uuid.UUID] = uuid_pk()
    tournament_id: Mapped[uuid.UUID] = mapped_column(
        _fk("tournaments.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("model_registry.id", ondelete="SET NULL"), index=True
    )

    key: Mapped[str] = mapped_column(sa.Text)
    model_slug: Mapped[str] = mapped_column(sa.Text)
    quantization: Mapped[str | None] = mapped_column(sa.Text)
    display_name: Mapped[str] = mapped_column(sa.Text)
    seed: Mapped[int] = mapped_column(default=0, server_default="0")
    withdrawn: Mapped[bool] = mapped_column(default=False, server_default=sa.false())

    created_at: Mapped[dt.datetime] = created_at()

    tournament: Mapped[Tournament] = relationship(back_populates="entrants")

    __table_args__ = (
        sa.UniqueConstraint("tournament_id", "key", name="uq_tournament_entrants_key"),
    )


class TournamentGame(Base):
    """One scheduled pairing, and the game that played it.

    The row exists **before** the game does. That is what makes a tournament resumable without
    replaying anything: the schedule is written down, and restarting asks "which of these has no
    finished game yet" rather than trusting a crashed process's memory. `game_id` stays null for a
    bye, which is a scheduled point rather than a game.
    """

    __tablename__ = "tournament_games"

    id: Mapped[uuid.UUID] = uuid_pk()
    tournament_id: Mapped[uuid.UUID] = mapped_column(
        _fk("tournaments.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[uuid.UUID | None] = mapped_column(
        _fk("games.id", ondelete="SET NULL"), index=True
    )

    round_number: Mapped[int] = mapped_column(index=True)
    white_key: Mapped[str] = mapped_column(sa.Text)
    #: Null for a bye.
    black_key: Mapped[str | None] = mapped_column(sa.Text)

    #: White's score once known: 1, 0.5 or 0. Null while the game is unplayed or in flight.
    white_score: Mapped[float | None] = mapped_column(sa.Float)
    #: Why this pairing has no usable result — a provider that could not be reached, a model
    #: withdrawn mid-event. Recorded rather than retried forever.
    abandoned_reason: Mapped[str | None] = mapped_column(sa.Text)

    created_at: Mapped[dt.datetime] = created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column()
    ended_at: Mapped[dt.datetime | None] = mapped_column()

    tournament: Mapped[Tournament] = relationship(back_populates="games")

    __table_args__ = (
        sa.UniqueConstraint(
            "tournament_id",
            "round_number",
            "white_key",
            "black_key",
            name="uq_tournament_games_pairing",
        ),
        sa.Index("ix_tournament_games_round", "tournament_id", "round_number"),
    )

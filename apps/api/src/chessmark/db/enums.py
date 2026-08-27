"""Enumerations for persisted state.

Chess-level enums (`Colour`, `GameResult`, `Termination`) live in `chessmark.game` and are reused
here — the domain is the single source of truth. These are the ones that only exist because we
store things.
"""

from __future__ import annotations

from enum import StrEnum


class GameStatus(StrEnum):
    PENDING = "pending"
    """Created, not yet started. No LLM call has been made."""

    RUNNING = "running"

    PAUSED = "paused"
    """Stopped by something outside the game, and expected to continue.

    A provider rate limit is the case this exists for. It is not a result and not a failure of
    either model: nobody played badly and the position is untouched, so recording it as `ABORTED`
    published a claim about a game that had simply not happened yet. A paused game keeps its
    transcript, holds no concurrency slot, and is picked up again by the reconciler once
    `resume_after` passes.
    """

    FINISHED = "finished"
    ABORTED = "aborted"
    """Cancelled by an admin or abandoned. Distinct from a game that reached a chess result."""


class PlayerKind(StrEnum):
    MODEL = "model"
    HUMAN = "human"
    ENGINE = "engine"
    """Stockfish, from Phase 14."""


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    FORFEITED = "forfeited"


class ModerationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"
    """Withheld from display but still stored — research integrity (TALK-05)."""


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class EventType(StrEnum):
    """The event log that drives live streaming, reconnect backfill, and replay (ADR-0008).

    These shapes are consumed by the frontend, so they are a compatibility surface. Adding a type
    is safe; changing the meaning of an existing one is not.
    """

    GAME_STARTED = "game_started"
    TURN_STARTED = "turn_started"
    THINKING = "thinking"
    #: Assistant prose that is not a tool call and not addressed to the opponent.
    #: Distinct from THINKING because providers split differently: DeepSeek puts everything in
    #: `reasoning`, Gemini puts everything in `content`, and collapsing the two would either hide
    #: half the models or mislabel the other half.
    OUTPUT = "output"
    TOOL_CALLED = "tool_called"
    ILLEGAL_ATTEMPT = "illegal_attempt"
    MOVE_MADE = "move_made"
    MESSAGE_SENT = "message_sent"
    DRAW_OFFERED = "draw_offered"
    GAME_ENDED = "game_ended"
    #: The model summarised its own earlier history to stay inside its context window
    #: (ADR-0018). Carries what was folded and what it cost, because a compaction changes what the
    #: model can see and a reader comparing two games needs to know it happened.
    COMPACTED = "compacted"
    #: The harness stopped the game and means to continue it — today, a provider rate limit.
    #: Carries the reason and when it will be tried again, because the alternative is a board that
    #: stops moving and a page with nothing to say about why.
    GAME_PAUSED = "game_paused"
    #: A game stopped by the harness — a budget, a ply cap, a provider outage — was reopened with
    #: room to continue. Never a chess result: those are final.
    GAME_RESUMED = "game_resumed"


class CreditReason(StrEnum):
    """Why a balance moved (AUTH-13).

    Stored rather than inferred: "the balance went down by two" does not say whether a game was
    started, an administrator took credits back, or we corrected our own mistake — and those are
    the three questions anyone auditing a balance actually has.
    """

    #: Spent to start a game. Carries the game it paid for.
    GAME_START = "game_start"
    #: An administrator adding credits. Carries who.
    ADMIN_GRANT = "admin_grant"
    #: An administrator taking them back.
    ADMIN_REVOKE = "admin_revoke"
    #: Given back for a game that never ran. Distinct from a grant because it undoes rather than
    #: decides — a refund is our mistake being corrected, not a decision about a person.
    REFUND = "refund"


class TournamentStatus(StrEnum):
    """Where a tournament is in its life.

    `PAUSED` is deliberately distinct from `FINISHED`: a tournament stopped by its own budget or
    by an operator still has rounds left to play, and saying so is what lets it be resumed rather
    than restarted.
    """

    #: Created, field resolved, nothing enqueued yet.
    PENDING = "pending"
    RUNNING = "running"
    #: Stopped with games left — budget reached, or halted by hand.
    PAUSED = "paused"
    #: Every round played.
    FINISHED = "finished"
    #: Abandoned. Its games stay readable; its standings are final but incomplete.
    ABANDONED = "abandoned"

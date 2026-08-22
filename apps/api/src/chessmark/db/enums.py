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

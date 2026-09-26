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


class ModelRuntime(StrEnum):
    """How a model is asked for its move (ADR-0049).

    A property of the *model*, not of the seat's kind: a decision model is still a `MODEL` seat,
    played by the worker, rated on the same leaderboard and grouped by the same registry row. What
    differs is the call that produces the move, and that is all this names.
    """

    LLM = "llm"
    """A chat model that acts through tools, one transcript per seat (AGENT-01, ADR-0003)."""

    DECISION = "decision"
    """A decision model on OpenRouter's Decisions API. It never writes text: it is handed the
    position and the legal moves, each described, and returns a probability for every move. No
    transcript, no tools, and so no way to propose an illegal move at all."""


class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    FORFEITED = "forfeited"

    #: The provider stopped answering part-way through, and the rounds it *did* complete were kept
    #: (ADR-0045). Distinct from `FAILED`, which is a turn that produced nothing worth keeping —
    #: this one holds real calls, real tool results and real spend, and the retry continues from it.
    #:
    #: It never carries a `ply_number`: no move came out of it, which is the same thing a forfeited
    #: turn says about itself and what keeps a ply-keyed index unambiguous.
    INTERRUPTED = "interrupted"


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
    #: A decision model answered (ADR-0049): the move it chose, the probability it gave every legal
    #: move, and — when a draw was on offer — the probability it gave accepting. The decision seat's
    #: equivalent of THINKING, and withheld from a person mid-game by the same rule (invariant 8),
    #: because a ranking of every move is as much a plan as a paragraph of reasoning is.
    DECIDED = "decided"
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

    #: Spent on one model turn, at what it actually cost (ADR-0052). Carries the game and the turn.
    TURN = "turn"
    #: Spent to start a game, when a credit was a unit of play (ADR-0016). No longer written.
    GAME_START = "game_start"
    #: An administrator adding credits. Carries who.
    ADMIN_GRANT = "admin_grant"
    #: An administrator taking them back.
    ADMIN_REVOKE = "admin_revoke"
    #: Given back for a game that never ran, under ADR-0016. No longer written: a game is charged
    #: for the turns it played and a turn that is rolled back is never charged, so there is nothing
    #: left to give back (ADR-0052).
    REFUND = "refund"
    #: The closing row of a balance held in credits, written once by the migration that made the
    #: balance dollars. Credits did not convert — the owner reset every balance to zero (ADR-0052).
    RETIRED = "retired"


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

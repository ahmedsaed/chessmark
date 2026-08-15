"""The turn loop: one agent, one move.

Within a turn the agent runs a bounded tool-use loop. It may read the board as often as it likes;
it ends by committing a move, by forfeiting, or by running out of budget.

Everything that happens is persisted as it happens — the LLM call verbatim, every tool call, every
transcript message, and an event for the live stream. A turn that crashes half way leaves a
truthful partial record rather than a gap.

The four ways a turn can fail are deliberately distinct, because the benchmark's whole point is
telling them apart:

* ``illegal_move_forfeit`` — the model could not produce a legal move with the full list in front
  of it (ADR-0002).
* ``error_forfeit`` — the model would not call a tool at all, even after being told.
* ``timeout`` / ``budget_exceeded`` — the harness stopped it.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import prompts, transcript
from chessmark.agents.llm import LlmGateway
from chessmark.agents.tools import ToolDispatcher, ToolName, TurnState, tool_schemas
from chessmark.agents.types import Completion, LlmError, ToolInvocation
from chessmark.db.enums import EventType, ModerationStatus, TurnStatus
from chessmark.db.models import Game, LlmCall, Message, Player, ToolCall, Turn
from chessmark.db.repositories import append_event, record_ply
from chessmark.game import Colour, MoveOutcome, Outcome, Referee, Termination


@dataclass(frozen=True, slots=True)
class TurnLimits:
    """Per-turn ceilings (AGENT-08). Exceeding any of them forfeits the turn."""

    max_tool_iterations: int = 12
    """LLM round-trips within one turn. A model looping on read-only tools must still terminate."""

    max_seconds: float = 180.0
    max_tokens: int = 200_000


@dataclass(slots=True)
class TurnResult:
    turn_id: int
    status: TurnStatus
    move: MoveOutcome | None = None
    outcome: Outcome | None = None
    """Set when the game ended on this turn."""

    illegal_attempts: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    said: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    latency_ms: int = 0
    error: str | None = None

    @property
    def moved(self) -> bool:
        return self.move is not None


async def ensure_system_prompt(
    session: AsyncSession,
    *,
    game: Game,
    player: Player,
    opponent_name: str,
) -> None:
    """Seed the transcript with the system prompt, once.

    Idempotent, and deliberately written exactly once per game: the system prompt is the head of
    the cached prefix, so re-rendering it mid-game would invalidate every turn after it.
    """
    if await transcript.transcript_length(session, player.id):
        return

    await transcript.append_message(
        session,
        player_id=player.id,
        game_id=game.id,
        role="system",
        content=prompts.build_system_prompt(
            colour=Colour(player.colour),
            opponent=opponent_name,
            max_illegal_retries=game.max_illegal_retries,
            trash_talk_enabled=game.trash_talk_enabled,
        ),
    )


class TurnRunner:
    """Runs one agent turn to completion."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        gateway: LlmGateway,
        referee: Referee,
        game: Game,
        player: Player,
        model: str,
        limits: TurnLimits | None = None,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.referee = referee
        self.game = game
        self.player = player
        self.model = model
        self.limits = limits or TurnLimits()

        self.colour = Colour(player.colour)
        self.state = TurnState()
        self.dispatcher = ToolDispatcher(
            referee=referee,
            colour=self.colour,
            state=self.state,
            max_illegal_retries=game.max_illegal_retries,
            trash_talk_enabled=game.trash_talk_enabled,
        )
        self._tools = tool_schemas(trash_talk_enabled=game.trash_talk_enabled)
        self._llm_sequence = 0
        self._tool_sequence = 0
        self._nudged = False
        self._move_committed = False

    # ------------------------------------------------------------------ entry point

    async def run(self) -> TurnResult:
        turn = Turn(
            game_id=self.game.id,
            player_id=self.player.id,
            status=TurnStatus.RUNNING,
        )
        self.session.add(turn)
        await self.session.flush()

        result = TurnResult(turn_id=turn.id, status=TurnStatus.RUNNING)
        started = time.perf_counter()

        await append_event(
            self.session,
            game_id=self.game.id,
            type=EventType.TURN_STARTED,
            payload={
                "player_id": str(self.player.id),
                "colour": self.colour.value,
                "ply": self.referee.ply + 1,
                "model": self.model,
            },
        )

        await transcript.append_message(
            self.session,
            player_id=self.player.id,
            game_id=self.game.id,
            turn_id=turn.id,
            role="user",
            content=prompts.TURN_PROMPT.format(ply=self.referee.ply + 1),
        )

        try:
            await self._loop(turn, result, started)
        except LlmError as error:
            result.status = TurnStatus.FAILED
            result.error = str(error)
            result.outcome = self._forfeit(
                Termination.ERROR_FORFEIT, f"Provider call failed: {error}"
            )

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        await self._finalise(turn, result)
        return result

    # ------------------------------------------------------------------ the loop

    async def _loop(self, turn: Turn, result: TurnResult, started: float) -> None:
        for _iteration in range(self.limits.max_tool_iterations):
            if self._over_budget(result, started):
                return

            messages = await transcript.build_messages(self.session, self.player.id)
            completion = await self.gateway.complete(
                model=self.model, messages=messages, tools=self._tools
            )

            await self._record_llm_call(turn, completion)
            self._accumulate(result, completion)

            if completion.reasoning:
                await append_event(
                    self.session,
                    game_id=self.game.id,
                    type=EventType.THINKING,
                    payload={
                        "player_id": str(self.player.id),
                        "reasoning": completion.reasoning,
                        "tokens": completion.usage.reasoning,
                    },
                )

            await transcript.append_message(
                self.session,
                player_id=self.player.id,
                game_id=self.game.id,
                turn_id=turn.id,
                role="assistant",
                content=completion.content,
                tool_calls=self._serialise_tool_calls(completion),
            )

            if not completion.tool_calls:
                if await self._nudge(turn, result):
                    continue
                return

            if await self._run_tool_calls(turn, result, completion.tool_calls):
                return

        # Ran out of iterations without moving.
        result.status = TurnStatus.FORFEITED
        result.outcome = self._forfeit(
            Termination.ERROR_FORFEIT,
            f"{self.colour.value.capitalize()} made {self.limits.max_tool_iterations} tool "
            "rounds without playing a move.",
        )

    async def _run_tool_calls(
        self, turn: Turn, result: TurnResult, calls: list[ToolInvocation]
    ) -> bool:
        """Execute every tool call in the response. Returns True when the turn is over.

        All calls get a result message even after a move is committed — providers require one per
        `tool_call_id`, and a missing result corrupts the transcript for every later turn.
        """
        for call in calls:
            if self._move_committed and call.name in {ToolName.MAKE_MOVE, ToolName.RESIGN}:
                await self._append_tool_result(
                    turn,
                    call,
                    {
                        "ok": False,
                        "error": "already_moved",
                        "detail": "You have already moved this turn.",
                    },
                    ok=False,
                )
                continue

            tool_result = self.dispatcher.execute(call)
            await self._append_tool_result(turn, call, tool_result.payload, ok=tool_result.ok)

            await append_event(
                self.session,
                game_id=self.game.id,
                type=EventType.ILLEGAL_ATTEMPT if tool_result.illegal else EventType.TOOL_CALLED,
                payload={
                    "player_id": str(self.player.id),
                    "tool": call.name,
                    "ok": tool_result.ok,
                    **(
                        {
                            "attempt": self.state.illegal_attempts,
                            "move": call.arguments.get("move"),
                            "detail": tool_result.payload.get("detail"),
                        }
                        if tool_result.illegal
                        else {}
                    ),
                },
            )

            if tool_result.message is not None:
                await self._record_said(turn, tool_result.message)

            if tool_result.illegal and self.dispatcher.retries_exhausted:
                result.status = TurnStatus.FORFEITED
                result.outcome = self._forfeit(
                    Termination.ILLEGAL_MOVE_FORFEIT,
                    f"{self.colour.value.capitalize()} failed to play a legal move in "
                    f"{self.state.illegal_attempts} attempts, with the full legal move list "
                    "provided each time.",
                )
                return True

            if tool_result.move is not None:
                self._move_committed = True
                result.move = tool_result.move
                result.outcome = tool_result.move.outcome
                await self._record_move(turn, tool_result.move)

            elif tool_result.ends_game:
                result.status = TurnStatus.COMPLETED
                result.outcome = self.referee.outcome
                return True

        if self._move_committed:
            result.status = TurnStatus.COMPLETED
            return True
        return False

    async def _nudge(self, turn: Turn, result: TurnResult) -> bool:
        """Tell a silent model to use its tools. Exactly one of these per turn (AGENT-05)."""
        if self._nudged:
            result.status = TurnStatus.FORFEITED
            result.outcome = self._forfeit(
                Termination.ERROR_FORFEIT,
                f"{self.colour.value.capitalize()} replied without calling a tool twice in a row.",
            )
            return False

        self._nudged = True
        await transcript.append_message(
            self.session,
            player_id=self.player.id,
            game_id=self.game.id,
            turn_id=turn.id,
            role="user",
            content=prompts.NUDGE_PROMPT,
        )
        return True

    # ------------------------------------------------------------------ persistence

    def _serialise_tool_calls(self, completion: Completion) -> list[dict[str, Any]] | None:
        if not completion.tool_calls:
            return None
        return [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.raw_arguments},
            }
            for call in completion.tool_calls
        ]

    async def _record_llm_call(self, turn: Turn, completion: Completion) -> None:
        self._llm_sequence += 1
        self.session.add(
            LlmCall(
                game_id=self.game.id,
                turn_id=turn.id,
                sequence=self._llm_sequence,
                model_slug=self.model,
                request=completion.request,
                response=completion.response,
                reasoning_text=completion.reasoning,
                prompt_tokens=completion.usage.prompt,
                completion_tokens=completion.usage.completion,
                reasoning_tokens=completion.usage.reasoning,
                cached_tokens=completion.usage.cached,
                cost_usd=completion.cost_usd,
                latency_ms=completion.latency_ms,
                finish_reason=completion.finish_reason,
            )
        )
        await self.session.flush()

    async def _append_tool_result(
        self, turn: Turn, call: ToolInvocation, payload: dict[str, Any], *, ok: bool
    ) -> None:
        self._tool_sequence += 1
        self.session.add(
            ToolCall(
                game_id=self.game.id,
                turn_id=turn.id,
                sequence=self._tool_sequence,
                name=call.name,
                arguments=call.arguments,
                result=payload,
                ok=ok,
            )
        )
        await transcript.append_message(
            self.session,
            player_id=self.player.id,
            game_id=self.game.id,
            turn_id=turn.id,
            role="tool",
            tool_call_id=call.id,
            name=call.name,
            content=json.dumps(payload, sort_keys=True),
        )

    async def _record_move(self, turn: Turn, move: MoveOutcome) -> None:
        await record_ply(
            self.session,
            game_id=self.game.id,
            colour=self.colour,
            move=move,
            turn_id=turn.id,
        )
        turn.ply_number = move.ply
        await append_event(
            self.session,
            game_id=self.game.id,
            type=EventType.MOVE_MADE,
            payload={
                "player_id": str(self.player.id),
                "colour": self.colour.value,
                "ply": move.ply,
                "san": move.move.san,
                "uci": move.move.uci,
                "fen": move.fen_after,
                "check": move.move.is_check,
            },
        )

    async def _record_said(self, turn: Turn, message: str) -> None:
        self.session.add(
            Message(
                game_id=self.game.id,
                player_id=self.player.id,
                turn_id=turn.id,
                ply_number=self.referee.ply,
                content=message,
                # Phase 11 replaces this with a real moderation decision (TALK-05).
                moderation_status=ModerationStatus.PENDING,
            )
        )
        await append_event(
            self.session,
            game_id=self.game.id,
            type=EventType.MESSAGE_SENT,
            payload={
                "player_id": str(self.player.id),
                "colour": self.colour.value,
                "content": message,
            },
        )

    async def _finalise(self, turn: Turn, result: TurnResult) -> None:
        result.illegal_attempts = self.state.illegal_attempts
        result.tool_calls = self.state.tool_calls
        result.llm_calls = self._llm_sequence
        result.said = list(self.state.said)

        if result.status is TurnStatus.RUNNING:
            result.status = TurnStatus.COMPLETED if result.moved else TurnStatus.FAILED

        turn.status = result.status
        turn.illegal_attempts = result.illegal_attempts
        turn.tool_call_count = result.tool_calls
        turn.llm_call_count = result.llm_calls
        turn.prompt_tokens = result.prompt_tokens
        turn.completion_tokens = result.completion_tokens
        turn.reasoning_tokens = result.reasoning_tokens
        turn.cached_tokens = result.cached_tokens
        turn.cost_usd = result.cost_usd
        turn.latency_ms = result.latency_ms
        turn.error = result.error
        turn.ended_at = sa.func.now()

        await self.session.execute(
            sa.update(Player)
            .where(Player.id == self.player.id)
            .values(
                illegal_attempts=Player.illegal_attempts + result.illegal_attempts,
                prompt_tokens=Player.prompt_tokens + result.prompt_tokens,
                completion_tokens=Player.completion_tokens + result.completion_tokens,
                reasoning_tokens=Player.reasoning_tokens + result.reasoning_tokens,
                cached_tokens=Player.cached_tokens + result.cached_tokens,
                total_cost_usd=Player.total_cost_usd + result.cost_usd,
                forfeited=Player.forfeited | (result.status is TurnStatus.FORFEITED),
            )
        )
        await self.session.execute(
            sa.update(Game)
            .where(Game.id == self.game.id)
            .values(
                total_cost_usd=Game.total_cost_usd + result.cost_usd,
                total_tokens=Game.total_tokens + result.prompt_tokens + result.completion_tokens,
            )
        )

        if result.outcome is not None:
            await append_event(
                self.session,
                game_id=self.game.id,
                type=EventType.GAME_ENDED,
                payload={
                    "result": str(result.outcome.result),
                    "termination": str(result.outcome.termination),
                    "detail": result.outcome.detail,
                    "winner": result.outcome.winner.value if result.outcome.winner else None,
                },
            )

        await self.session.flush()

    # ------------------------------------------------------------------ helpers

    def _accumulate(self, result: TurnResult, completion: Completion) -> None:
        result.prompt_tokens += completion.usage.prompt
        result.completion_tokens += completion.usage.completion
        result.reasoning_tokens += completion.usage.reasoning
        result.cached_tokens += completion.usage.cached
        result.cost_usd += completion.cost_usd

    def _over_budget(self, result: TurnResult, started: float) -> bool:
        elapsed = time.perf_counter() - started
        if elapsed > self.limits.max_seconds:
            result.status = TurnStatus.FORFEITED
            result.outcome = self._forfeit(
                Termination.TIMEOUT,
                f"{self.colour.value.capitalize()} exceeded the {self.limits.max_seconds:.0f}s "
                "turn limit.",
            )
            return True

        used = result.prompt_tokens + result.completion_tokens
        if used > self.limits.max_tokens:
            result.status = TurnStatus.FORFEITED
            result.outcome = self._forfeit(
                Termination.BUDGET_EXCEEDED,
                f"{self.colour.value.capitalize()} used {used} tokens in one turn, over the "
                f"{self.limits.max_tokens} limit.",
            )
            return True

        return False

    def _forfeit(self, termination: Termination, detail: str) -> Outcome | None:
        """End the game against this player, unless it is already over."""
        if self.referee.is_over:
            return self.referee.outcome
        return self.referee.forfeit(self.colour, termination, detail)


__all__ = [
    "TurnLimits",
    "TurnResult",
    "TurnRunner",
    "ensure_system_prompt",
    "uuid",
]

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
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import compaction, prompts, transcript
from chessmark.agents.llm import LlmGateway
from chessmark.agents.mangled import ProviderMangledError, mangled_tool_call
from chessmark.agents.sessions import session_for_game
from chessmark.agents.tools import ToolDispatcher, ToolName, TurnState, tool_schemas
from chessmark.agents.types import Completion, LlmError, RateLimit, ToolInvocation
from chessmark.db.enums import EventType, ModerationStatus, TurnStatus
from chessmark.db.models import Game, LlmCall, Message, Player, ToolCall, Turn
from chessmark.db.repositories import append_event, record_ply
from chessmark.game import Colour, MoveOutcome, Outcome, Referee, Termination

log = logging.getLogger(__name__)

#: How many truncated responses a model may produce in one turn before forfeiting. More than one
#: because the first truncation is often a model discovering how long its own reasoning runs; a
#: model that cannot act in three attempts, having been told each time, is genuinely stuck.
MAX_TRUNCATIONS = 3

#: How many times a model may reply without calling a tool before forfeiting the turn (AGENT-05).
#:
#: Was one. A single nudge cost two real games — `nemotron-3-nano-omni-30b-a3b-reasoning:free`
#: forfeited twice, both times having answered in prose — and one instruction is a thin basis for
#: recording a loss against a model, because the first prose reply is often a reasoning model
#: narrating before it acts rather than declining to act at all.
#:
#: Three, and counted the same way `MAX_TRUNCATIONS` is: three nudges are sent, and the fourth
#: toolless reply in one turn ends it. A model that has been told four times, in the same turn,
#: that prose does not move a piece is not going to move one.
MAX_NUDGES = 3


@dataclass(frozen=True, slots=True)
class TurnLimits:
    """Per-turn ceilings (AGENT-08).

    **These are circuit breakers, not a budget that shapes play.** Every value is set well above
    what a strong model legitimately needs, so a model is never made to play worse to stay inside
    them. They exist to stop a runaway — an unbounded reasoning spiral, a tool loop that never
    terminates — from consuming a whole game's cost in one move.

    Sizing them around what weak models can manage would quietly turn the benchmark into a test
    of brevity. If a capable model is hitting one of these, the cap is wrong, not the model.
    """

    max_tool_iterations: int = 20
    """LLM round-trips within one turn.

    A model that reads the board, enumerates legal moves, reconsiders, and retries a rejected move
    can legitimately use ten or more. This bounds a loop that never terminates, nothing tighter.
    """

    #: **There is no per-turn clock, deliberately.** There was one — 600 seconds — and it measured
    #: the wrong thing: a turn makes an unknown number of calls (2.95 per ply for free models), so a
    #: shared time budget punished a slow-but-healthy sequence and blamed whichever call happened to
    #: exhaust it. Worse, the turn's *remaining* seconds were handed to each call as its deadline, so
    #: a turn with 583 of 600 spent asked for a completion in 17 — below a free model's mean latency
    #: — and the doomed call was reported as `provider call exceeded 17s`, took the abandon path, and
    #: cost a real game at ply 10.
    #:
    #: A turn is already bounded by `max_tool_iterations` (how many calls) and
    #: `max_completion_budget` (how much it may generate), and each call is bounded by the gateway's
    #: own ten-minute timeout — which now *pauses* the game rather than abandoning it (ADR-0017).
    #: Seconds-per-turn added nothing to those three and contradicted the last one.

    max_completion_budget: int = 400_000
    """**Completion** tokens across the whole turn, summed over every round-trip.

    It counted prompt tokens too, and the prompt is re-sent on every round-trip (ADR-0003) — so the
    ceiling measured transcript size times round-trips, both of which are the harness's doing. A model
    that produced 5,263 tokens was forfeited for "using 514,446": four replays of a 128k transcript.
    It punished long games hardest, because that is where the transcript is largest, and
    big-context models hardest of all — a 1M-window model at a 128k prompt is nowhere near
    compaction's trigger and four round-trips still crossed a flat 400k.

    Output is the model's own contribution and the only honest measure of a runaway. 400k tokens of
    *output* in one turn is pathological; 400k of replayed prompt is an ordinary Tuesday at ply 70.
    """

    max_completion_tokens: int = 64_000
    """A ceiling on one answer, **clamped at call time** to what the endpoint will accept.

    Unclamped it was a flat 64,000 reconciled against nothing, which asked a 65,536-token endpoint
    for 65,810 tokens and was refused — a 400 that abandoned a game at ply 10. See
    `compaction.Window.completion_cap`.
    """

    context_reserve_tokens: int = compaction.DEFAULT_RESERVE_TOKENS
    """Room held back for the next completion, and so the trigger point (ADR-0018).

    Held back rather than spent, which is what lets the summarising call answer at all: compaction
    fires while the prompt still fits, and this is the space it fits in.
    """

    keep_turns: int = compaction.DEFAULT_KEEP_TURNS
    """How many recent turns survive a compaction verbatim (ADR-0018).

    **Turns, not messages.** A turn is three to five messages and every provider rejects a `tool`
    result whose `tool_calls` parent is missing, so a count of messages would cut mid-turn and 400.
    """
    """Cap on a *single* response.

    The per-turn budget cannot bound this: it is only checked between round-trips, so by the time
    it is consulted the tokens are already generated and billed. Only a per-call ceiling stops a
    spiral mid-flight.

    Sized as a circuit breaker rather than an allowance. A legitimate turn was observed using
    ~9,800 completion tokens; a spiral used 34,260 and still produced no tool call. 64,000 leaves
    a frontier model room to think as hard as it wants about a sharp position while still
    catching genuinely unbounded generation.
    """


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
    #: Set when the turn failed because a provider asked us to come back later. The orchestrator
    #: reads this rather than the error text: a rate limit is the one provider failure that should
    #: pause the game instead of counting against its retry budget.
    rate_limit: RateLimit | None = None
    #: The provider rejected the request itself. Requeueing it cannot help.
    request_rejected: bool = False

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
        opponent: Player,
        model: str,
        limits: TurnLimits | None = None,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.referee = referee
        self.game = game
        self.player = player
        self.opponent = opponent
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
        self._nudges = 0
        #: The prompt size the provider last reported. Exact where an estimate would do, and the
        #: reason compaction fires on measurement rather than on a per-ply guess.
        self._prompt_tokens = 0
        self._cached_window: compaction.Window | None = None
        self._compactions = 0
        self._truncations = 0
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
            await self._loop(turn, result)
        except LlmError as error:
            # A provider failure is **our** problem, not the model's, so the game is left open.
            # AGENT-09 says transient provider errors must not count against a model, and
            # forfeiting here would do exactly that: a rate limit, an outage, or an exhausted
            # daily quota would be recorded as `error_forfeit` and read on the leaderboard as the
            # model failing to operate. Observed for real — an OpenRouter daily cap ended a turn
            # mid-game and would have handed the opponent a win.
            #
            # The turn is marked FAILED and the referee is untouched. The orchestrator decides
            # what to do about it (retry the turn, or abandon the game as `aborted`) — Phase 5.
            result.status = TurnStatus.FAILED
            result.error = str(error)
            result.outcome = None
            result.rate_limit = error.rate_limit
            result.request_rejected = error.request_rejected
        except ProviderMangledError as error:
            # The endpoint failed to parse a tool call the model did make (ADR-0015). Same
            # treatment as an outage, for the same reason: the model acted correctly and its host
            # did not, so forfeiting it would publish a claim about the model that the endpoint
            # manufactured. `deepseek-v4-pro` lost two games this way through StreamLake and none
            # at all through Baidu or DeepInfra, on identical weights at identical precision.
            result.status = TurnStatus.FAILED
            result.error = str(error)
            result.outcome = None

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        await self._finalise(turn, result)
        return result

    # ------------------------------------------------------------------ context

    async def _endpoint_window(self) -> compaction.Window:
        """What the endpoint serving this seat accepts. Resolved once per turn and remembered.

        Once, because the seat is pinned for the whole game (ADR-0015) so the answer cannot change
        mid-turn — and a query per round-trip would repeat it three to five times a ply.
        """
        if self._cached_window is None:
            only = (self.player.provider_routing or {}).get("only") or []
            self._cached_window = await compaction.window_for(
                self.session,
                model_slug=self.model,
                provider=str(only[0]) if only else None,
                reserve=self.limits.context_reserve_tokens,
            )
        return self._cached_window

    async def _compact(
        self, turn: Turn, result: TurnResult, occupied: int, window: compaction.Window
    ) -> bool:
        """Ask the model to summarise its own earlier turns. True when the transcript shrank.

        Failure returns False and the turn proceeds on the un-compacted history, which will very
        likely be refused for exceeding the window — and that is the intended outcome. The turn
        fails, rolls back, and is retried, which compacts again; a rate limit pauses the game
        instead. The alternative, carrying on regardless, spends a call to be told the prompt is
        too large and then abandons the game.
        """
        rows = await compaction.live_messages(self.session, self.player.id)
        plan = compaction.plan_compaction(rows, keep_turns=self.limits.keep_turns)
        if not plan.worthwhile:
            # Nothing left to fold: the retained turns alone fill the window. Compacting again
            # cannot help, so say so rather than looping on it.
            log.warning(
                "cannot compact %s: the last %d turns already fill the window",
                self.player.id,
                self.limits.keep_turns,
            )
            return False

        log.info(
            "compacting %s at %d of %d tokens: folding %d messages, keeping %d",
            self.player.id,
            occupied,
            window.context,
            len(plan.fold),
            len(plan.keep),
        )

        completion = await self.gateway.complete(
            model=self.model,
            messages=compaction.summary_request(plan),
            # No tools: a model handed its schema mid-summary calls one, and the call would have to
            # be discarded. And a small cap, which is what keeps this request inside the window it
            # exists to make room in — the reserve the trigger held back is exactly this space.
            max_tokens=window.completion_cap(occupied, compaction.SUMMARY_MAX_TOKENS),
            session_id=session_for_game(self.game.id),
        )
        await self._record_llm_call(turn, completion)
        self._accumulate(result, completion)

        summary = (completion.content or "").strip()
        if not summary:
            log.warning(
                "compaction of %s produced nothing; leaving the transcript alone", self.player.id
            )
            return False

        await compaction.apply(
            self.session,
            player_id=self.player.id,
            game_id=self.game.id,
            plan=plan,
            summary=summary,
        )
        self._compactions += 1
        self._prompt_tokens = 0  # the next call measures the new prefix rather than the old one

        await append_event(
            self.session,
            game_id=self.game.id,
            type=EventType.COMPACTED,
            payload={
                "player_id": str(self.player.id),
                "colour": self.colour.value,
                "model": self.model,
                "folded": len(plan.fold),
                "kept": len(plan.keep),
                "occupied_tokens": occupied,
                "context_tokens": window.context,
                "summary_tokens": completion.usage.completion,
                "compaction": self._compactions,
            },
        )
        return True

    # ------------------------------------------------------------------ the loop

    async def _loop(self, turn: Turn, result: TurnResult) -> None:
        for _iteration in range(self.limits.max_tool_iterations):
            if self._over_budget(result):
                return

            messages = await transcript.build_messages(self.session, self.player.id)

            # How full the window is. The provider's own count from the previous round-trip when
            # there is one — exact, per invariant 4 — and a character estimate before the first,
            # which is the only point in a turn where nothing exact exists.
            occupied = self._prompt_tokens or compaction.estimate_tokens(messages)
            window = await self._endpoint_window()

            # **Once per turn.** A turn is a few round-trips against one transcript, so a second
            # compaction inside it would be folding what the first just wrote — and on a window
            # smaller than the reserve the trigger is true even straight after a fold, which would
            # spend a call per round-trip discovering there is nothing left to fold.
            if (
                self._compactions == 0
                and window.should_compact(occupied)
                and await self._compact(turn, result, occupied, window)
            ):
                # Rebuilt from the summary, so both the message list and its size change.
                messages = await transcript.build_messages(self.session, self.player.id)
                occupied = compaction.estimate_tokens(messages)

            completion = await self.gateway.complete(
                model=self.model,
                messages=messages,
                tools=self._tools,
                # **Clamped to what the endpoint will accept.** A flat 64,000 reconciled against
                # nothing asked a 65,536-token endpoint for 65,810 tokens and was refused — a 400
                # that abandoned a game at ply 10. `max_tokens` is a ceiling on the answer, so
                # asking for less than the model needs is a truncation and asking for more than
                # fits is a rejection; this is the largest value that cannot be rejected.
                max_tokens=window.completion_cap(occupied, self.limits.max_completion_tokens),
                # One session per game, both seats included, so a match reads as a conversation
                # on OpenRouter's own dashboard rather than as a hundred unrelated generations.
                # See `agents/sessions.py` for why the unit is the game and not the turn.
                session_id=session_for_game(self.game.id),
            )

            await self._record_llm_call(turn, completion)
            self._accumulate(result, completion)
            self._prompt_tokens = completion.usage.prompt or self._prompt_tokens

            if completion.reasoning:
                # Written in full, always. Whether a *reader* may see it is decided on the way out,
                # in `api/redaction.py`: withheld from a person while their own game is live
                # (invariant 8, HUMAN-07), published to spectators of a model-vs-model game, and
                # revealed to everyone once the game is over.
                #
                # It used to be dropped here instead, for any game with a human seat. The log is
                # append-only (ADR-0008), so that made the omission permanent — a person's own
                # games were the only ones whose reasoning the transcript could never show, long
                # after there was anything left to leak.
                await append_event(
                    self.session,
                    game_id=self.game.id,
                    type=EventType.THINKING,
                    payload={
                        "player_id": str(self.player.id),
                        "tokens": completion.usage.reasoning,
                        "reasoning": completion.reasoning,
                    },
                )

            if completion.content and completion.content.strip():
                # Gemini says everything here and nothing in `reasoning`; DeepSeek does the exact
                # opposite. Emitting only one of the two made an entire model look silent — 43 of
                # Gemini's 83 calls in the first paid benchmark carried prose that never reached
                # the page. Held back from a live human opponent for the same reason reasoning is,
                # and by the same gate on the way out.
                await append_event(
                    self.session,
                    game_id=self.game.id,
                    type=EventType.OUTPUT,
                    payload={
                        "player_id": str(self.player.id),
                        "content": completion.content,
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
                # Stored so the next turn can hand it straight back. Gemini 3 rejects a function
                # call whose `thought_signature` is missing and DeepSeek rejects a thinking-mode
                # history without its `reasoning_content`; both travel in here.
                reasoning_details=completion.reasoning_details,
            )

            if not completion.tool_calls:
                if await self._no_action(turn, result, completion):
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
                    "args": call.arguments,
                    "result": tool_result.payload,
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

    async def _no_action(self, turn: Turn, result: TurnResult, completion: Completion) -> bool:
        """A response with no tool call. Returns True to try again, False to end the turn.

        *Why* there was no tool call matters, and conflating the two cases would put a harness
        limit into the benchmark as a model failure:

        * ``finish_reason == "length"`` — the model was cut off mid-reasoning and never reached the
          point of acting. Telling it "you did not call a tool" would be untrue, and forfeiting it
          would blame the model for an output budget it does not control. Observed live:
          gpt-oss-20b:free spent 32,753 reasoning tokens, hit its provider's output ceiling, and
          was forfeited for a refusal it never made.
        * the response carries tool-call *markup* but no structured tool calls — the model tried
          to act and its endpoint failed to parse it. That is the host's failure, not the model's
          (ADR-0015), so the game is abandoned rather than forfeited.
        * anything else — the model finished its turn and chose not to act. That is the failure
          AGENT-05 is about.
        """
        if completion.finish_reason == "length":
            return await self._retry_truncated(turn, result)
        if mangled_tool_call(completion):
            raise ProviderMangledError(self.model, completion)
        return await self._nudge(turn, result)

    async def _retry_truncated(self, turn: Turn, result: TurnResult) -> bool:
        self._truncations += 1
        if self._truncations > MAX_TRUNCATIONS:
            result.status = TurnStatus.FORFEITED
            result.outcome = self._forfeit(
                Termination.TRUNCATED,
                f"{self.colour.value.capitalize()} was cut off by the output limit "
                f"{self._truncations} times without ever acting.",
            )
            return False

        await transcript.append_message(
            self.session,
            player_id=self.player.id,
            game_id=self.game.id,
            turn_id=turn.id,
            role="user",
            content=prompts.TRUNCATED_PROMPT,
        )
        return True

    async def _nudge(self, turn: Turn, result: TurnResult) -> bool:
        """Tell a silent model to use its tools. Up to `MAX_NUDGES` per turn (AGENT-05)."""
        self._nudges += 1
        if self._nudges > MAX_NUDGES:
            result.status = TurnStatus.FORFEITED
            result.outcome = self._forfeit(
                Termination.ERROR_FORFEIT,
                f"{self.colour.value.capitalize()} replied without calling a tool "
                f"{self._nudges} times in a row.",
            )
            return False

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
                provider=completion.provider,
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
        await self._deliver_to_opponent(message)

    async def _deliver_to_opponent(self, message: str) -> None:
        """Put the message into the opponent's transcript (TALK-02).

        Without this, `say` broadcasts into a void — models would talk *at* spectators and never
        to each other, which is the entire reason ADR-0009 chose a standalone tool over a comment
        field on `make_move`.

        Delivered immediately rather than gathered at the start of the opponent's next turn, so it
        lands in the natural place: after whatever the opponent last did, before its next turn
        prompt. Appending to a transcript that is not currently being read is safe — every
        transcript is append-only, and the opponent rebuilds from `seq` when its turn comes.

        The opponent's system prompt is seeded first if it has not played yet, so an opening taunt
        cannot end up as row 1, ahead of the prompt that heads the cached prefix.
        """
        await ensure_system_prompt(
            self.session,
            game=self.game,
            player=self.opponent,
            opponent_name=self.player.display_name,
        )
        await transcript.append_message(
            self.session,
            player_id=self.opponent.id,
            game_id=self.game.id,
            role="user",
            content=prompts.OPPONENT_SAID.format(message=message),
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
                compactions=Player.compactions + self._compactions,
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

        # `game_ended` is deliberately *not* emitted here. The turn knows the referee reached a
        # terminal state, but concluding the game — flipping its status, recording the result — is
        # the orchestrator's job, and whoever owns that transition owns announcing it. Emitting
        # from both produced two `game_ended` events for every game, which a spectator would
        # render twice and a replay would show twice.
        await self.session.flush()

    # ------------------------------------------------------------------ helpers

    def _accumulate(self, result: TurnResult, completion: Completion) -> None:
        result.prompt_tokens += completion.usage.prompt
        result.completion_tokens += completion.usage.completion
        result.reasoning_tokens += completion.usage.reasoning
        result.cached_tokens += completion.usage.cached
        result.cost_usd += completion.cost_usd

    def _over_budget(self, result: TurnResult) -> bool:
        """Whether this turn has generated more than it may.

        It used to police a wall clock too, and that clock is gone (see `TurnLimits`). What is left
        is the one bound that measures the model rather than the provider: **completion** tokens, not
        the prompt, because the prompt is re-sent every round-trip (ADR-0003) and counting it made
        the ceiling a function of transcript size times round-trips.
        """
        if result.completion_tokens > self.limits.max_completion_budget:
            result.status = TurnStatus.FORFEITED
            result.outcome = self._forfeit(
                Termination.BUDGET_EXCEEDED,
                f"{self.colour.value.capitalize()} generated {result.completion_tokens} tokens in "
                f"one turn, over the {self.limits.max_completion_budget} limit.",
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

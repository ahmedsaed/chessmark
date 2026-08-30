"""Summarising a game's own history to stay inside the model's context window (ADR-0018).

The transcript is replayed whole on every turn (ADR-0003) and grows about **1,818 tokens per ply**,
measured. A 128k window therefore covers roughly seventy plies of a possible three hundred, and a
talkative model reaches the wall sooner than that. Raising the floor only moves the wall; it is
still there, and `context_exceeded` is a *forfeit* — a loss recorded against a model for running out
of room rather than for playing badly.

So the agent does what an agent does: at a threshold it summarises its own earlier turns and carries
on from the summary.

**Chess makes this unusually safe.** The server is the only authority on board state (invariant 1)
and the model can always call `get_board_state` and `get_legal_moves`. A summary that loses detail
cannot corrupt the game, because nothing the model believes about the position is load-bearing —
which is not true of a general agent whose context *is* its world.

Three things this deliberately is not:

* **Not a rewrite of the record.** `transcript_messages` keeps every row it ever held; folded rows
  get a `superseded_at` and stop being sent. Invariant 3 asks that the record be verbatim, and it
  still is — what changed is the request.
* **Not free of invariant 2.** Compaction rewrites the cacheable prefix by definition, so it costs
  one cache miss. That is why it cuts *deep* rather than to just under the threshold: compacting to
  89% would mean compacting again three plies later and paying repeatedly.
* **Not written by anyone else.** The model summarises its own history, on its own pinned endpoint,
  and the call is recorded in `llm_calls` like every other. A cheaper third model would be cheaper
  and would put another model's prose in a benchmark record.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.db.models import TranscriptMessage

log = logging.getLogger(__name__)

#: How many of the most recent turns are kept verbatim.
#:
#: **Turns, not messages.** A turn is three to five messages — an assistant message requesting
#: tools, the tool results, then the move — and every provider rejects a `tool` message whose
#: `tool_calls` parent is missing. Counting messages would cut mid-turn and 400.
DEFAULT_KEEP_TURNS = 4

#: Room reserved for the next completion. The trigger fires while the prompt still *fits*, so the
#: summarising call has this much space to answer in, which is what stops compaction failing for
#: the very reason it exists.
DEFAULT_RESERVE_TOKENS = 20_000

#: A fraction of the window below which compaction is not worth its cache miss.
MIN_FRACTION = 0.10

#: The summary is a paragraph or two, not an essay. It is also the cap that keeps the compacting
#: call inside the window it is trying to make room in.
SUMMARY_MAX_TOKENS = 2_000

#: The smallest answer worth asking for. Below this, `completion_cap` raises rather than clamping.
#:
#: **The floor used to be 1**, on the reasoning that "a request for zero output is not a request".
#: That is true of zero and false of one: a `max_tokens` of 1 is not a smaller request, it is a
#: request that cannot succeed. Fed a prompt size larger than the window it asked an endpoint for a
#: single token, every reply came back `finish_reason: "length"`, and after four of them a model was
#: forfeited for truncation at ply 5 — a harness miscalculation published as a finding about a
#: player, which is precisely what ADR-0019 exists to prevent.
MIN_USEFUL_COMPLETION = 1_024

#: What a game's first call may ask for, as a fraction of the window, before anything is measured.
#:
#: **A bound, not an estimate.** Half a window cannot be wrong in the dangerous direction: the
#: system prompt plus one turn prompt is a few thousand tokens against a window of at least 64k
#: (AGENT-14), so the sum always fits. It costs at most a shorter first answer, where the old
#: character estimate cost a game.
FIRST_CALL_FRACTION = 2


SUMMARY_INSTRUCTION = """\
Your context window is nearly full, so summarise the game so far.

Write a compact briefing to your future self covering: the opening and its name if you know it, the
plan you have been following, the pawn structure and any weaknesses on both sides, material, and
anything you decided earlier that you want to remember. Do not list every move — the moves are
recorded and the board is authoritative.

Reply with the summary only. Do not call a tool."""

#: Framing for the summary when it is replayed. Told as something *given* to the model rather than
#: something it said, because it must not read as its own recollection: the model needs to know its
#: history was folded so it re-reads the board rather than trusting a paraphrase of it.
SUMMARY_PREAMBLE = """\
Your earlier turns in this game have been summarised to stay inside your context window. The
summary you wrote follows. The moves so far and the current position are authoritative and
available through your tools — call `get_board_state` if you need the position.

--- summary of the game so far ---
{summary}
--- end of summary ---"""


class NoRoomToAnswerError(Exception):
    """The prompt leaves no usable room for a completion.

    Raised rather than returned so it cannot be ignored by a caller that treats the cap as just a
    number. It is not a provider failure and not a model failure: it says the transcript needs to
    get smaller before this endpoint can be asked anything at all.
    """

    def __init__(self, *, context: int, prompt_tokens: int, available: int) -> None:
        super().__init__(
            f"a {context}-token window holding a {prompt_tokens}-token prompt leaves "
            f"{available} tokens to answer in, under the {MIN_USEFUL_COMPLETION} floor"
        )
        self.context = context
        self.prompt_tokens = prompt_tokens
        self.available = available


@dataclass(frozen=True, slots=True)
class Window:
    """What the endpoint serving this seat will accept.

    `context` of 0 means unknown, and unknown disables compaction rather than guessing: a provider
    that declares nothing is not a provider to make arithmetic about.
    """

    context: int = 0
    reserve: int = DEFAULT_RESERVE_TOKENS

    @property
    def known(self) -> bool:
        return self.context > 0

    def headroom_needed(self) -> int:
        """The point at which the prompt is too close to the window to send another completion.

        The two rules people reach for — "within 20k of the limit" and "past 90%" — are the same
        idea from opposite ends: one is a completion reserve, the other a percentage. Taking the
        larger scales across a 64k window and a 1M one without a special case.
        """
        return max(self.reserve, int(self.context * MIN_FRACTION))

    def should_compact(self, prompt_tokens: int) -> bool:
        if not self.known or prompt_tokens <= 0:
            return False
        return self.context - prompt_tokens < self.headroom_needed()

    def completion_cap(self, prompt_tokens: int | None, requested: int) -> int:
        """How many output tokens may be asked for, given what the prompt already occupies.

        The clamp that had been missing (AGENT-16). `max_tokens` was a flat 64,000 reconciled
        against nothing, so a 65,536-token endpoint was asked for 65,810 tokens and refused — a 400
        that abandoned a game at ply 10.

        `prompt_tokens` is `None` when nothing has been measured yet, which happens on a game's
        first call and nowhere else. We do **not** guess there: a bound of half the window is used
        instead, and a bound cannot be wrong in the direction that forfeits a model. What used to
        happen was a character estimate, and it is worth being exact about how badly that went — it
        reported 477,155 tokens for a six-ply transcript against a 256,000-token window, the clamp
        went negative, its `max(1, ...)` floor asked for one output token, and the model was
        forfeited for the truncations that followed (ADR-0021).

        Raises `NoRoomToAnswerError` rather than returning a number too small to answer in. That is a
        state for the caller to act on — compact, or stop — not a value to pass to a provider.
        """
        if not self.known:
            return requested
        if prompt_tokens is None:
            return max(1, min(requested, self.context // FIRST_CALL_FRACTION))

        # 256 for the framing the provider adds around our messages: role markers, the tool schema's
        # envelope, whatever a given endpoint counts that we cannot see.
        available = self.context - prompt_tokens - 256
        if available < MIN_USEFUL_COMPLETION:
            raise NoRoomToAnswerError(
                context=self.context, prompt_tokens=prompt_tokens, available=available
            )
        return min(requested, available)


@dataclass(frozen=True, slots=True)
class Plan:
    """Which messages get folded and which are kept verbatim."""

    fold: list[TranscriptMessage]
    keep: list[TranscriptMessage]

    @property
    def worthwhile(self) -> bool:
        """Whether there is anything to gain.

        Nothing to fold means the retained turns alone already fill the window — compaction cannot
        help, and pretending it did would loop. The caller treats that as "cannot compact" rather
        than as success.
        """
        return bool(self.fold)


async def live_messages(session: AsyncSession, player_id: uuid.UUID) -> list[TranscriptMessage]:
    """Every message that is still replayed for this player, in send order.

    Ordered explicitly rather than by `seq`: the system prompt first, then the live summary, then
    the retained turns. `seq` is append-only, so a summary written at ply 60 holds the *highest*
    sequence number in the table and would otherwise replay after the turns it summarises.
    """
    rows = list(
        await session.scalars(
            sa.select(TranscriptMessage)
            .where(
                TranscriptMessage.player_id == player_id,
                TranscriptMessage.superseded_at.is_(None),
            )
            .order_by(TranscriptMessage.seq)
        )
    )
    rows = [r for r in rows if is_sendable(r)]
    system = [r for r in rows if r.role == "system"]
    summary = [r for r in rows if r.is_summary]
    rest = [r for r in rows if r.role != "system" and not r.is_summary]
    return system + summary + rest


def is_sendable(row: TranscriptMessage) -> bool:
    """Whether this row may be put in a request at all.

    One rule: an assistant message needs *something* in it. `{"role": "assistant"}` with no content
    and no tool calls is refused outright by Liquid — *"Assistant messages require `content`,
    `tool_calls`, or `function_call`"* — and the transcript is append-only, so one such row refuses
    every later turn of that seat until somebody edits the database.

    The row is no longer written (`turn.py`), and this is the second line: it repairs a transcript
    that already holds one, and it covers the summary request as well as the turn's, because both
    build their messages from `live_messages`. A row with nothing in it has nothing to replay.
    """
    return row.role != "assistant" or bool(row.content) or bool(row.tool_calls)


async def window_for(
    session: AsyncSession,
    *,
    model_slug: str,
    provider: str | None,
    reserve: int = DEFAULT_RESERVE_TOKENS,
) -> Window:
    """What the endpoint serving this seat will accept.

    The **endpoint's** window, not the model's advertised one, because they differ and the endpoint
    is what refuses: the 400 that abandoned a game said "this endpoint's maximum context length is
    65536" while its model advertised more. Both numbers are stored; this reads the one that binds.

    Falls back to the model's figure when the endpoint declares nothing, and to unknown when
    neither does — and unknown disables compaction rather than guessing.
    """
    from chessmark.db.models import ModelEndpoint, ModelRegistry

    query = (
        sa.select(ModelEndpoint.context_length, ModelRegistry.context_length)
        .join(ModelRegistry, ModelRegistry.id == ModelEndpoint.model_id)
        .where(ModelRegistry.openrouter_id == model_slug)
    )
    if provider:
        query = query.where(ModelEndpoint.provider_name == provider)

    row = (await session.execute(query.limit(1))).first()
    if row is None:
        model_context = await session.scalar(
            sa.select(ModelRegistry.context_length).where(ModelRegistry.openrouter_id == model_slug)
        )
        return Window(context=int(model_context or 0), reserve=reserve)

    endpoint_context, model_context = row
    return Window(context=int(endpoint_context or model_context or 0), reserve=reserve)


def plan_compaction(rows: list[TranscriptMessage], *, keep_turns: int = DEFAULT_KEEP_TURNS) -> Plan:
    """Fold everything except the system prompt and the last `keep_turns` turns.

    The cut lands on a turn boundary, so a `tool` result is never separated from the assistant
    message that requested it. A previous summary is folded into the new one, which keeps exactly
    one live summary and means the builder never has to order several of them.

    Messages with no `turn_id` — a human's action, anything written outside a turn — are kept if
    they fall after the cut and folded if before, on the same rule as everything else.
    """
    turn_ids: list[int] = []
    for row in rows:
        if row.turn_id is not None and row.turn_id not in turn_ids:
            turn_ids.append(row.turn_id)

    keep_ids = set(turn_ids[-keep_turns:]) if keep_turns > 0 else set()
    cut = min((r.seq for r in rows if r.turn_id in keep_ids), default=None)

    fold: list[TranscriptMessage] = []
    keep: list[TranscriptMessage] = []
    for row in rows:
        # The system prompt is never folded: it is the byte-stable head of the cacheable prefix
        # (ADR-0003) and the only message a compaction must leave exactly where it was.
        retained = row.role == "system" or (cut is not None and row.seq >= cut)
        (keep if retained else fold).append(row)
    return Plan(fold=fold, keep=keep)


def summary_request(plan: Plan) -> list[dict[str, Any]]:
    """The message list sent to ask for the summary.

    The history being folded, plus the instruction. Tools are deliberately **not** offered: a model
    handed its own tool schema mid-summary tends to call one, and a tool call here would have to be
    discarded, which wastes the call and confuses the transcript.
    """
    from chessmark.agents.transcript import to_provider_message

    messages = [to_provider_message(row) for row in plan.fold]
    messages.append({"role": "user", "content": SUMMARY_INSTRUCTION})
    return messages


async def apply(
    session: AsyncSession,
    *,
    player_id: uuid.UUID,
    game_id: uuid.UUID,
    plan: Plan,
    summary: str,
    now: dt.datetime | None = None,
) -> TranscriptMessage:
    """Fold the planned messages and append the summary. One statement each; no deletes."""
    stamp = now or dt.datetime.now(dt.UTC)
    for row in plan.fold:
        row.superseded_at = stamp

    # Through the same allocator as every other append: `players.transcript_seq` under a row lock,
    # not `max(seq) + 1`. Two sources of truth for one sequence is a unique-violation waiting for
    # the first concurrent append, and the counter is the one the rest of the turn uses.
    from chessmark.agents.transcript import append_message

    return await append_message(
        session,
        game_id=game_id,
        player_id=player_id,
        role="user",
        content=SUMMARY_PREAMBLE.format(summary=summary.strip()),
        is_summary=True,
    )


__all__ = [
    "DEFAULT_KEEP_TURNS",
    "DEFAULT_RESERVE_TOKENS",
    "MIN_USEFUL_COMPLETION",
    "SUMMARY_MAX_TOKENS",
    "NoRoomToAnswerError",
    "Plan",
    "Window",
    "apply",
    "live_messages",
    "plan_compaction",
    "summary_request",
]

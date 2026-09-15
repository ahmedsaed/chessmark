"""The invariants, checked against games that were actually played.

CLAUDE.md lists twelve rules that "if broken, quietly ruin the project", and **nothing checked them
against a finished game.** Every test in this suite asserts one thing about one path; the failures
that reached production were properties of a *whole game* that no single test was looking at.

The dangling tool call is the case that argues for this file. `max_closing_rounds` was checked
before `_run_tool_calls` rather than after, so a turn that spent its closing rounds calling tools
appended an assistant message carrying `tool_calls` and returned without running them. Every test
of the turn loop passed. `make check` was green. It corrupted **242 transcript rows across 14
seats** before a strict endpoint refused one and the game died at ply 2 — and the shape that
produced it is three lines of scripted model.

So: play games with models that behave the way real ones do, then ask the database the questions
the invariants ask. The models here are not adversarial inventions — each one is a behaviour taken
from a game in production, named for the model that did it.

**No provider and no network.** `agents/scripted.py` plugs in as `LlmGateway(completion_fn=...)`,
so the real turn loop, the real referee, real tool dispatch and real persistence all run with only
the provider replaced.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.scripted import prose, scripted, step, tool_call
from chessmark.agents.turn import TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import GameEvent, LlmCall, Player, TranscriptMessage
from chessmark.db.models import Turn as TurnRow
from chessmark.game import Colour
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration


# ============================================================ how models actually behave
#
# Each of these is a real behaviour, from a real game, named for the model that produced it. A
# scripted model invented from imagination tests imagination; these test the free tier.


def keeps_calling_after_moving() -> list[dict[str, object]]:
    """`9450f060`, every turn: move, read the board, then call `make_move` again.

    The shape that produced the dangling tool call. The last call is answered by nothing unless the
    turn runs its calls *before* deciding it is over.
    """
    return [
        step(tool_call("get_legal_moves")),
        step(tool_call("make_move", move="e4")),
        step(tool_call("get_board")),
        step(tool_call("make_move", move="e4")),
        step(tool_call("get_move_history")),
    ]


def truncated_then_moves() -> list[dict[str, object]]:
    """`nemotron-3-nano`: cut off mid-thought at the endpoint's ceiling, then plays."""
    return [
        step(content="I should consider " * 200, finish_reason="length", completion_tokens=900),
        step(tool_call("make_move", move="e4")),
    ]


def silent_then_moves() -> list[dict[str, object]]:
    """`nemotron-3.5-lightning`: prose with no tool call, then a real move."""
    return [prose("Let me think about this position."), step(tool_call("make_move", move="e4"))]


def illegal_then_legal() -> list[dict[str, object]]:
    """`north-mini-code`, which spent 16 games doing this."""
    return [
        step(tool_call("make_move", move="Qh5")),
        step(tool_call("make_move", move="Ke2")),
        step(tool_call("make_move", move="e4")),
    ]


def repeats_one_question() -> list[dict[str, object]]:
    """The loop ADR-0026 exists for: the same read-only call until something changes the answer."""
    return [
        step(tool_call("get_board")),
        step(tool_call("get_board")),
        step(tool_call("get_board")),
        step(tool_call("make_move", move="e4")),
    ]


BEHAVIOURS = {
    "keeps calling after moving": keeps_calling_after_moving,
    "truncated then moves": truncated_then_moves,
    "silent then moves": silent_then_moves,
    "illegal then legal": illegal_then_legal,
    "repeats one question": repeats_one_question,
}


# ============================================================================== the questions


async def dangling_tool_calls(db: AsyncSession, player_id: object) -> list[int]:
    """Assistant rows whose `tool_calls` nothing answered — invariant 2's practical edge.

    The transcript is append-only, so one of these refuses **every later turn of that seat**: no
    pause, retry or resume clears it. `TOOL_CALLS_MISSING_RESULTS` is what a strict endpoint says
    about it; a tolerant one plays on and accumulates them, which is why there were 242.
    """
    rows = list(
        await db.scalars(
            sa.select(TranscriptMessage)
            .where(TranscriptMessage.player_id == player_id)
            .order_by(TranscriptMessage.seq)
        )
    )
    answered = {r.tool_call_id for r in rows if r.role == "tool" and r.tool_call_id}
    return [
        row.seq
        for row in rows
        if row.role == "assistant"
        for call in (row.tool_calls or [])
        if call.get("id") not in answered
    ]


async def unsendable_rows(db: AsyncSession, player_id: object) -> list[int]:
    """Assistant rows with neither content nor tool calls.

    Liquid refuses these outright — *"Assistant messages require `content`, `tool_calls`, or
    `function_call`"* — and one abandoned a real game at ply 57 (ADR-0021).
    """
    rows = await db.scalars(
        sa.select(TranscriptMessage).where(
            TranscriptMessage.player_id == player_id,
            TranscriptMessage.role == "assistant",
            TranscriptMessage.superseded_at.is_(None),
        )
    )
    return [
        row.seq for row in rows if not (row.content or "").strip() and not (row.tool_calls or [])
    ]


# ================================================================== one seat, every behaviour


@pytest.mark.parametrize("behaviour", list(BEHAVIOURS), ids=list(BEHAVIOURS))
class TestATurnLeavesASendableTranscript:
    """**The transcript a turn leaves behind must be one a provider will accept.**

    Asserted per behaviour rather than once, because the bug was in one branch of the turn loop and
    a single happy-path game would not have entered it. Every one of these passed every existing
    test.
    """

    async def test_no_tool_call_is_left_unanswered(
        self, db: AsyncSession, table: Table, behaviour: str
    ) -> None:
        await play_turn(db, table, scripted(*BEHAVIOURS[behaviour]()), colour=Colour.WHITE)

        dangling = await dangling_tool_calls(db, table.white.id)
        assert dangling == [], (
            f"seq {dangling} carry `tool_calls` nothing answered. The transcript is append-only, "
            "so this seat is now refused for the rest of the game"
        )

    async def test_no_row_is_unsendable(
        self, db: AsyncSession, table: Table, behaviour: str
    ) -> None:
        await play_turn(db, table, scripted(*BEHAVIOURS[behaviour]()), colour=Colour.WHITE)

        assert await unsendable_rows(db, table.white.id) == []

    async def test_the_transcript_alternates_the_way_a_provider_expects(
        self, db: AsyncSession, table: Table, behaviour: str
    ) -> None:
        """A `tool` row must follow the assistant row that asked for it — no orphans."""
        await play_turn(db, table, scripted(*BEHAVIOURS[behaviour]()), colour=Colour.WHITE)

        rows = list(
            await db.scalars(
                sa.select(TranscriptMessage)
                .where(TranscriptMessage.player_id == table.white.id)
                .order_by(TranscriptMessage.seq)
            )
        )
        asked = {
            call.get("id")
            for row in rows
            if row.role == "assistant"
            for call in (row.tool_calls or [])
        }
        orphans = [r.seq for r in rows if r.role == "tool" and r.tool_call_id not in asked]
        assert orphans == [], f"seq {orphans} answer a call nothing made"


# ============================================================== a whole game, every invariant


async def play_a_game(db: AsyncSession, table: Table, plies: int) -> None:
    """A game where both seats misbehave, alternating colours."""
    moves = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7"]
    names = list(BEHAVIOURS)
    for ply in range(plies):
        colour = Colour.WHITE if ply % 2 == 0 else Colour.BLACK
        behaviour = BEHAVIOURS[names[ply % len(names)]]()
        # The scripted move is fixed per behaviour, so rewrite it to this ply's legal one.
        patched = [
            step(tool_call("make_move", move=moves[ply]))
            if (s.get("choices") or [{}])[0].get("message", {}).get("tool_calls")
            and (s["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "make_move")
            else s
            for s in behaviour
        ]
        await play_turn(db, table, scripted(*patched), colour=colour)


class TestAPlayedGameHoldsTheInvariants:
    """Ten plies, both seats misbehaving, then the questions CLAUDE.md asks."""

    async def test_neither_seat_ends_with_a_broken_transcript(
        self, db: AsyncSession, table: Table
    ) -> None:
        await play_a_game(db, table, plies=10)

        for player in (table.white, table.black):
            assert await dangling_tool_calls(db, player.id) == [], player.colour
            assert await unsendable_rows(db, player.id) == [], player.colour

    async def test_every_move_appended_exactly_one_event(
        self, db: AsyncSession, table: Table
    ) -> None:
        """Invariant 7. Live, reconnect and replay all read that one table, so a second row is a
        move shown twice and a missing one is a move that never happened."""
        await play_a_game(db, table, plies=10)

        moves = list(
            await db.scalars(
                sa.select(GameEvent.payload).where(
                    GameEvent.game_id == table.game.id, GameEvent.type == "move_made"
                )
            )
        )
        plies = [int(p["ply"]) for p in moves]
        assert plies == sorted(plies), "the log is out of order"
        assert len(plies) == len(set(plies)) == 10, f"{len(plies)} events for 10 moves"

    async def test_cost_is_the_sum_of_what_the_provider_returned(
        self, db: AsyncSession, table: Table
    ) -> None:
        """Invariant 4. Never an estimate — and never a number nobody can trace to a call."""
        await play_a_game(db, table, plies=10)

        for player in (table.white, table.black):
            # Through `turns`, because an `llm_calls` row belongs to a turn and a turn belongs to
            # a seat — there is no shortcut, and inventing one is how a total starts counting the
            # opponent's tokens.
            counted = await db.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(LlmCall.prompt_tokens), 0))
                .select_from(LlmCall)
                .join(TurnRow, TurnRow.id == LlmCall.turn_id)
                .where(TurnRow.player_id == player.id)
            )
            stored = await db.scalar(sa.select(Player.prompt_tokens).where(Player.id == player.id))
            assert stored == counted, (
                f"{player.colour} carries {stored} prompt tokens; its calls returned {counted}"
            )

    async def test_every_recorded_call_has_its_raw_payload(
        self, db: AsyncSession, table: Table
    ) -> None:
        """Invariant 3. If a number appears on the leaderboard its transcript is one click away,
        which is only true while every call kept what it sent and what came back."""
        await play_a_game(db, table, plies=10)

        calls = list(await db.scalars(sa.select(LlmCall).where(LlmCall.game_id == table.game.id)))
        assert calls, "a played game with no recorded calls is not a record of anything"
        bare = [c.id for c in calls if not c.request or not c.response]
        assert bare == [], f"calls {bare} kept no payload"


class TestTheBoundsDoNotCorruptWhatTheyStop:
    """A harness ceiling fails a turn; it must not leave the seat unusable (invariant 11).

    The dangling tool call was exactly this failure — a bound firing in the wrong place — so the
    bounds get their own case rather than riding on the behaviours above.
    """

    @pytest.mark.parametrize("bound", [1, 2, 3, 4])
    async def test_a_closing_round_bound_still_answers_its_calls(
        self, db: AsyncSession, table: Table, bound: int
    ) -> None:
        """Parametrised over the bound because the bug needed the model to call a tool *at* it.
        A single value would have entered one branch and passed."""
        await play_turn(
            db,
            table,
            scripted(*keeps_calling_after_moving(), repeat_last=True),
            colour=Colour.WHITE,
            limits=TurnLimits(max_closing_rounds=bound),
        )

        assert await dangling_tool_calls(db, table.white.id) == [], f"at bound {bound}"

    async def test_a_tool_iteration_bound_still_answers_its_calls(
        self, db: AsyncSession, table: Table
    ) -> None:
        await play_turn(
            db,
            table,
            scripted(step(tool_call("get_board")), repeat_last=True),
            colour=Colour.WHITE,
            limits=TurnLimits(max_tool_iterations=3),
        )

        assert await dangling_tool_calls(db, table.white.id) == []


# ================================================ a turn a provider interrupted (ADR-0045)


def answers_then_stops(rounds: int) -> object:
    """A seat that completes `rounds` rounds and is then refused — a contended endpoint.

    The shape ADR-0045 is about, and the one that used to leave nothing behind: every round here is
    a real call with a real payload and real spend, and all of it was discarded.
    """
    served = {"calls": 0}

    async def complete(**_kwargs: object) -> object:
        served["calls"] += 1
        if served["calls"] <= rounds:
            return step(tool_call("get_board"))
        raise RefusedError

    return complete


class RefusedError(Exception):
    status_code = 429

    def __init__(self) -> None:
        super().__init__(
            'litellm.RateLimitError: OpenrouterException - {"error":{"message":"Provider returned '
            'error","code":429,"metadata":{"limit_source":"upstream_provider_shared_pool"}}}'
        )


class TestAnInterruptedTurnKeepsItsWork:
    """**A turn keeps the rounds it completed** (ADR-0045).

    The turn was one transaction and a provider failure raised out of it, so a turn refused on its
    third call discarded the two that had been answered and billed. The retry then paid for them
    again — in production `f129b600` that is the gaps in the turn ids, 7854-7856 and 7859, each a
    fresh attempt redoing the board read the attempt before it had completed.
    """

    async def test_the_calls_that_were_answered_are_recorded(
        self, db: AsyncSession, table: Table
    ) -> None:
        """Invariant 3. They happened, they were billed, and they had payloads."""
        result = await play_turn(db, table, answers_then_stops(2), colour=Colour.WHITE)
        assert result.keep_rounds, "a refusal between rounds should keep what came before it"

        calls = list(await db.scalars(sa.select(LlmCall).where(LlmCall.game_id == table.game.id)))
        assert len(calls) == 2, f"{len(calls)} of 2 answered calls survived the failure"
        assert all(call.request and call.response for call in calls), "a call kept no payload"

    async def test_the_turn_is_interrupted_rather_than_failed(
        self, db: AsyncSession, table: Table
    ) -> None:
        """`FAILED` and `INTERRUPTED` are the same instruction to the orchestrator and different
        facts about the record: one produced nothing, the other holds work the next attempt uses."""
        result = await play_turn(db, table, answers_then_stops(2), colour=Colour.WHITE)
        assert result.keep_rounds, "a refusal between rounds should keep what came before it"

        turns = list(
            await db.scalars(sa.select(TurnRow).where(TurnRow.player_id == table.white.id))
        )
        assert [t.status for t in turns] == [TurnStatus.INTERRUPTED]
        assert turns[0].ply_number is None
        assert turns[0].llm_call_count == 2

    async def test_the_transcript_it_leaves_is_sendable(
        self, db: AsyncSession, table: Table
    ) -> None:
        """**The invariant that replaced "roll the turn back".**

        The rollback existed because a half-written turn can leave an assistant message whose
        `tool_calls` nothing answered — append-only, so that seat is refused for the rest of the
        game, and it corrupted 242 rows before a strict endpoint noticed. Keeping the rounds is
        only safe while this holds, and the refusal lands *between* rounds, never inside one.
        """
        result = await play_turn(db, table, answers_then_stops(2), colour=Colour.WHITE)
        assert result.keep_rounds, "a refusal between rounds should keep what came before it"

        assert await dangling_tool_calls(db, table.white.id) == []
        assert await unsendable_rows(db, table.white.id) == []

    async def test_the_next_attempt_continues_instead_of_starting_over(
        self, db: AsyncSession, table: Table
    ) -> None:
        """The whole point. The retry must not re-ask what was already answered, and must not
        append a second turn prompt — the model would read "It is your move" twice with its own
        dead exchange between them."""
        result = await play_turn(db, table, answers_then_stops(2), colour=Colour.WHITE)
        assert result.keep_rounds, "a refusal between rounds should keep what came before it"
        interrupted_rounds = result.llm_calls

        await play_turn(
            db, table, scripted(step(tool_call("make_move", move="e4"))), colour=Colour.WHITE
        )

        rows = list(
            await db.scalars(
                sa.select(TranscriptMessage)
                .where(TranscriptMessage.player_id == table.white.id)
                .order_by(TranscriptMessage.seq)
            )
        )
        prompts = [r for r in rows if r.role == "user" and "your move" in (r.content or "")]
        assert len(prompts) == 1, f"the turn prompt was appended {len(prompts)} times"

        turns = list(
            await db.scalars(
                sa.select(TurnRow).where(TurnRow.player_id == table.white.id).order_by(TurnRow.id)
            )
        )
        assert len(turns) == 1, "the retry opened a second turn instead of continuing the first"
        assert turns[0].status is TurnStatus.COMPLETED
        assert turns[0].llm_call_count > interrupted_rounds, (
            "the row counts only the last attempt — the rounds of both belong to the turn, and "
            "`max_tool_iterations` is meant to bound the turn rather than each attempt at it"
        )

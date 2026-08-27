"""Compaction: the model summarising its own history to stay inside its window (ADR-0018).

Two properties matter more than the rest, and both are about what compaction must *not* break:

* **the cut lands on a turn boundary**, because every provider rejects a `tool` result whose
  `tool_calls` parent has gone — a count of messages rather than turns would 400;
* **nothing is deleted.** `transcript_messages` is the record of what we replayed (invariant 3), so
  a compacted game keeps every row it ever held and simply stops sending the folded ones.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import compaction, transcript
from chessmark.agents.compaction import (
    DEFAULT_RESERVE_TOKENS,
    Window,
    estimate_tokens,
    plan_compaction,
)
from chessmark.db.models import TranscriptMessage


class Row:
    """A stand-in for a transcript row.

    `plan_compaction` is pure and reads four attributes; `summary_request` serialises through
    `to_provider_message`, which reads the message fields too — so the double carries both sets
    rather than only what the planner needs.
    """

    def __init__(self, seq: int, role: str, turn_id: int | None, is_summary: bool = False) -> None:
        self.seq = seq
        self.role = role
        self.turn_id = turn_id
        self.is_summary = is_summary
        self.content = f"message {seq}"
        self.tool_calls = None
        self.reasoning_details = None
        self.tool_call_id = None
        self.name = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Row({self.seq}, {self.role}, turn={self.turn_id})"


def transcript_of(turns: int) -> list[Any]:
    """A system prompt then `turns` turns of user → assistant(tool_calls) → tool → assistant."""
    rows: list[Any] = [Row(1, "system", None)]
    seq = 2
    for turn_id in range(1, turns + 1):
        for role in ("user", "assistant", "tool", "assistant"):
            rows.append(Row(seq, role, turn_id))
            seq += 1
    return rows


# ====================================================================== when to compact


class TestTheTrigger:
    def test_it_fires_before_the_window_is_full_not_after(self) -> None:
        """The reserve is what makes the summarising call possible: it needs room to answer in, and
        a trigger that waited for the window to be full would leave none."""
        window = Window(context=200_000, reserve=DEFAULT_RESERVE_TOKENS)

        assert not window.should_compact(150_000)
        assert window.should_compact(185_000)

    def test_the_reserve_and_the_percentage_are_the_same_rule(self) -> None:
        """ "Within 20k of the limit" and "past 90%" are one idea from opposite ends. Taking the
        larger scales from a 64k window to a 1M one with no special case."""
        small = Window(context=64_000)
        large = Window(context=1_000_000)

        assert small.headroom_needed() == DEFAULT_RESERVE_TOKENS, "the reserve dominates"
        assert large.headroom_needed() == 100_000, "10% dominates"

    def test_an_unknown_window_never_compacts(self) -> None:
        """A provider that declares nothing is not one to do arithmetic about, and compacting on a
        guess would fold a transcript that had plenty of room."""
        assert not Window(context=0).should_compact(1_000_000)

    def test_no_measurement_yet_never_compacts(self) -> None:
        assert not Window(context=64_000).should_compact(0)


class TestTheCompletionCap:
    """The clamp that was missing. A flat 64,000 against a 65,536-token endpoint asked for 65,810
    tokens and was refused — a 400 that abandoned a game at ply 10."""

    def test_it_never_exceeds_what_is_left(self) -> None:
        window = Window(context=65_536)

        cap = window.completion_cap(1_810, 64_000)

        assert cap < 64_000
        assert 1_810 + cap < 65_536, "prompt plus completion must fit, which is what 400s"

    def test_it_asks_for_no_more_than_requested(self) -> None:
        """A ceiling, not a target: plenty of room does not mean asking for a longer answer."""
        assert Window(context=1_000_000).completion_cap(1_000, 4_000) == 4_000

    def test_it_never_asks_for_nothing(self) -> None:
        """A request for zero output is not a request, and a full window should fail as a rejection
        the worker can classify rather than as an empty answer nobody can read."""
        assert Window(context=1_000).completion_cap(5_000, 64_000) == 1

    def test_an_unknown_window_passes_the_request_through(self) -> None:
        assert Window(context=0).completion_cap(1_000, 64_000) == 64_000


def test_the_estimate_over_states_rather_than_under() -> None:
    """It is used only before a turn's first response, where nothing exact exists. Over-estimating
    compacts a little early; under-estimating hits the window and forfeits."""
    messages = [{"role": "user", "content": "x" * 3_500}]

    assert estimate_tokens(messages) >= 1_000


# ====================================================================== what to fold


class TestThePlan:
    def test_the_cut_lands_on_a_turn_boundary(self) -> None:
        """The property that stops a 400. Every retained `tool` message must still have the
        assistant message that requested it, which is only true if whole turns are kept."""
        rows = transcript_of(10)

        plan = plan_compaction(rows, keep_turns=4)

        kept_turns = {r.turn_id for r in plan.keep if r.turn_id is not None}
        assert kept_turns == {7, 8, 9, 10}
        for turn_id in kept_turns:
            roles = [r.role for r in plan.keep if r.turn_id == turn_id]
            assert roles == ["user", "assistant", "tool", "assistant"], "a whole turn, or none"

    def test_the_system_prompt_is_never_folded(self) -> None:
        """It is the byte-stable head of the cacheable prefix (ADR-0003) and the one message a
        compaction must leave exactly where it was."""
        plan = plan_compaction(transcript_of(10), keep_turns=2)

        assert plan.keep[0].role == "system"
        assert all(r.role != "system" for r in plan.fold)

    def test_a_previous_summary_is_folded_into_the_new_one(self) -> None:
        """So there is exactly one live summary. Several would need ordering rules the builder does
        not have, and would each cost their own tokens forever."""
        rows = [
            Row(1, "system", None),
            Row(2, "user", None, is_summary=True),
            *transcript_of(6)[1:],
        ]

        plan = plan_compaction(rows, keep_turns=2)

        assert any(r.is_summary for r in plan.fold)
        assert not any(r.is_summary for r in plan.keep)

    def test_nothing_to_fold_is_reported_rather_than_pretended(self) -> None:
        """The retained turns alone filling the window is a real state, and treating it as a
        successful compaction would loop forever."""
        plan = plan_compaction(transcript_of(3), keep_turns=4)

        assert not plan.worthwhile

    def test_keeping_nothing_folds_everything_but_the_system_prompt(self) -> None:
        plan = plan_compaction(transcript_of(5), keep_turns=0)

        assert [r.role for r in plan.keep] == ["system"]


def test_the_summary_request_carries_the_folded_history_and_asks_for_prose() -> None:
    """Tools are deliberately not offered: a model handed its schema mid-summary calls one, and the
    call would have to be discarded."""
    plan = plan_compaction(transcript_of(8), keep_turns=4)

    messages = compaction.summary_request(plan)

    assert len(messages) == len(plan.fold) + 1
    assert messages[-1]["role"] == "user"
    assert "summarise" in messages[-1]["content"]
    assert "Do not call a tool" in messages[-1]["content"]


# ====================================================================== against the database


@pytest.mark.integration
class TestFoldingForReal:
    async def _seed(self, db: AsyncSession, table: Any, turns: int) -> None:
        seq = 1
        db.add(
            TranscriptMessage(
                game_id=table.game.id,
                player_id=table.white.id,
                seq=seq,
                role="system",
                content="You are playing chess.",
            )
        )
        for turn_id in range(1, turns + 1):
            for role, content in (
                ("user", f"It is your move. Ply {turn_id}."),
                ("assistant", f"Thinking about ply {turn_id}."),
            ):
                seq += 1
                db.add(
                    TranscriptMessage(
                        game_id=table.game.id,
                        player_id=table.white.id,
                        seq=seq,
                        # `turn_id` is a real FK, so the shape is faked with NULLs and the plan is
                        # exercised over `seq` alone — which is what the cut actually uses.
                        turn_id=None,
                        role=role,
                        content=content,
                    )
                )
        table.white.transcript_seq = seq
        await db.commit()

    async def test_folded_rows_stop_being_sent_and_are_still_there(
        self, db: AsyncSession, table: Any
    ) -> None:
        """The whole bargain: the request shrinks, the record does not."""
        await self._seed(db, table, turns=6)
        rows = await compaction.live_messages(db, table.white.id)
        plan = plan_compaction(rows, keep_turns=0)

        await compaction.apply(
            db,
            player_id=table.white.id,
            game_id=table.game.id,
            plan=plan,
            summary="A Sicilian. I am a pawn up and my king is safe.",
        )
        await db.commit()

        sent = await transcript.build_messages(db, table.white.id)
        stored = await transcript.full_history(db, table.white.id)

        assert len(sent) < len(stored), "the request shrank"
        assert len(stored) == 13 + 1, "every original row is still stored, plus the summary"
        folded = await db.scalar(
            sa.select(sa.func.count())
            .select_from(TranscriptMessage)
            .where(
                TranscriptMessage.player_id == table.white.id,
                TranscriptMessage.superseded_at.is_not(None),
            )
        )
        assert folded == len(plan.fold)

    async def test_the_summary_replays_after_the_system_prompt(
        self, db: AsyncSession, table: Any
    ) -> None:
        """`seq` is append-only, so a summary written at ply 60 holds the highest sequence number in
        the table and would replay *after* the turns it summarises. Ordering is explicit for that
        reason, and getting it wrong would show the model its own summary as the newest thing said.
        """
        await self._seed(db, table, turns=4)
        rows = await compaction.live_messages(db, table.white.id)

        await compaction.apply(
            db,
            player_id=table.white.id,
            game_id=table.game.id,
            plan=plan_compaction(rows, keep_turns=0),
            summary="The Italian Game.",
        )
        await db.commit()

        sent = await transcript.build_messages(db, table.white.id)

        assert sent[0]["role"] == "system"
        assert "The Italian Game." in str(sent[1]["content"])

    async def test_the_model_is_told_it_was_compacted(self, db: AsyncSession, table: Any) -> None:
        """It must not read as its own recollection. A model that thinks a paraphrase is its memory
        acts on it; one that knows it was summarised re-reads the board, which is authoritative."""
        await self._seed(db, table, turns=4)
        rows = await compaction.live_messages(db, table.white.id)

        await compaction.apply(
            db,
            player_id=table.white.id,
            game_id=table.game.id,
            plan=plan_compaction(rows, keep_turns=0),
            summary="Anything.",
        )
        await db.commit()

        summary = str((await transcript.build_messages(db, table.white.id))[1]["content"])

        assert "summarised" in summary
        assert "get_board_state" in summary, "and told where the truth is"


# ====================================================================== a whole turn


@pytest.mark.integration
class TestATurnThatCompacts:
    """The end of it: a real turn, a real trigger, a real move afterwards.

    Everything except the provider is real — the referee, the tool dispatch, the transcript, the
    event log. The scripted model answers the summary request with prose and then plays.
    """

    async def _big_transcript(self, db: AsyncSession, table: Any, *, rows: int) -> None:
        """A transcript large enough to trip a small reserve, with real turn ids so the cut has
        boundaries to land on."""
        from chessmark.db.models import Turn as TurnRow

        seq = 1
        db.add(
            TranscriptMessage(
                game_id=table.game.id,
                player_id=table.white.id,
                seq=seq,
                role="system",
                content="You are playing chess.",
            )
        )
        for index in range(rows):
            turn_row = TurnRow(game_id=table.game.id, player_id=table.white.id)
            db.add(turn_row)
            await db.flush()
            for role in ("user", "assistant"):
                seq += 1
                db.add(
                    TranscriptMessage(
                        game_id=table.game.id,
                        player_id=table.white.id,
                        seq=seq,
                        turn_id=turn_row.id,
                        role=role,
                        content=f"{role} on turn {index}. " + ("board talk " * 300),
                    )
                )
        # `seq` comes from `players.transcript_seq` under a row lock, not from `max(seq)`. Seeding
        # rows without moving the counter makes the turn's own first append collide on seq 1.
        table.white.transcript_seq = seq
        await db.commit()

    async def _register(self, db: AsyncSession, *, slug: str, context: int) -> None:
        from chessmark.agents.registry import sync_model_registry
        from chessmark.db.models import ModelEndpoint, ModelRegistry

        await sync_model_registry(
            db,
            [{"openrouter_id": slug, "display_name": slug, "context_length": context}],
        )
        await db.flush()
        model_id = await db.scalar(
            sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slug)
        )
        db.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name="TestProvider",
                context_length=context,
                supports_tools=True,
                is_active=True,
            )
        )
        await db.commit()

    async def test_it_compacts_and_then_plays(self, db: AsyncSession, table: Any) -> None:
        from chessmark.agents.scripted import prose, scripted, step, tool_call
        from chessmark.agents.turn import TurnLimits
        from chessmark.db.enums import EventType, TurnStatus
        from chessmark.db.models import GameEvent
        from tests.agents.conftest import play_turn

        slug = "scripted/roomy"
        await self._register(db, slug=slug, context=60_000)
        await self._big_transcript(db, table, rows=10)

        before = len(await transcript.full_history(db, table.white.id))

        result = await play_turn(
            db,
            table,
            scripted(
                prose("A quiet Sicilian. I am a pawn up, my king is castled, his is not."),
                step(tool_call("make_move", move="e4")),
            ),
            model=slug,
            limits=TurnLimits(context_reserve_tokens=50_000, keep_turns=2),
        )

        assert result.status is TurnStatus.COMPLETED, "the turn still played its move"
        assert result.move is not None and result.move.move.san == "e4"
        assert result.llm_calls == 2, "one summary, one move — and the summary is a recorded call"

        # The record grew; the request shrank.
        after = await transcript.full_history(db, table.white.id)
        assert len(after) > before, "the summary was appended, nothing was removed"
        sent = await transcript.build_messages(db, table.white.id)
        assert len(sent) < len(after)
        assert any("summarised" in str(m.get("content")) for m in sent)

        events = list(
            await db.scalars(
                sa.select(GameEvent).where(
                    GameEvent.game_id == table.game.id, GameEvent.type == EventType.COMPACTED
                )
            )
        )
        assert len(events) == 1, "exactly one event per state change (invariant 7)"
        assert events[0].payload["folded"] > 0
        assert events[0].payload["context_tokens"] == 60_000

    async def test_a_roomy_window_never_compacts(self, db: AsyncSession, table: Any) -> None:
        """The common case, and the one that must stay cheap: no extra call, no event, no fold."""
        from chessmark.agents.scripted import scripted, step, tool_call
        from chessmark.db.enums import EventType, TurnStatus
        from chessmark.db.models import GameEvent
        from tests.agents.conftest import play_turn

        slug = "scripted/enormous"
        await self._register(db, slug=slug, context=1_000_000)

        result = await play_turn(
            db, table, scripted(step(tool_call("make_move", move="d4"))), model=slug
        )

        assert result.status is TurnStatus.COMPLETED
        assert result.llm_calls == 1, "no summarising call"
        assert not list(
            await db.scalars(sa.select(GameEvent).where(GameEvent.type == EventType.COMPACTED))
        )

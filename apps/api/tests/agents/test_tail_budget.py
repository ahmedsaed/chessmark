"""What compaction keeps is bounded by size, not by message count (ADR-0033).

A message count is a fact about the transcript's *shape* and says nothing about its weight.
Measured on two real games under the same twelve-message cap:

    10fc99f0   kept 12 messages = 606,376 characters   ~210,000 tokens of a 256,000 window
    545dc41a   kept 11 messages =  66,755 characters   ~ 23,000 tokens

The larger filled 82% of its window with the one region compaction may not touch, which is the
deadlock that left `29e7f004` and `e601f9af` unrecoverable: no room to write the summary that would
have shrunk it, and nothing else able to shrink it either.

The budget is in tokens because that is the unit the window is in, and it is *apportioned* by a
ratio measured on the conversation itself — the provider's token count for the last prompt over our
own character count of the rows that made it. Every comparable harness estimates this with a
tokeniser; a ratio calibrated on this endpoint and this transcript is a better estimate than one
calibrated on English prose.
"""

from __future__ import annotations

import pytest

from chessmark.agents.compaction import (
    CLAMP_HEAD_CHARACTERS,
    CLAMP_TAIL_CHARACTERS,
    CLAMP_THRESHOLD_CHARACTERS,
    KEEP_TAIL_TOKENS,
    clamped_content,
    plan_compaction,
    sent_characters,
)

#: Roughly what our transcripts measure — 2.89 characters per token across seven games.
RATIO = 1 / 2.89


class Row:
    """A stand-in for a transcript row, carrying the fields the planner and renderer read."""

    def __init__(self, seq: int, role: str, turn_id: int | None, content: str = "") -> None:
        self.seq = seq
        self.role = role
        self.turn_id = turn_id
        self.is_summary = False
        self.content = content or f"message {seq}"
        self.tool_calls = None
        self.reasoning_details = None
        self.tool_call_id = None
        self.name = None
        self.trimmed_at = None
        self.truncated_at = None
        self.clamped_at = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Row({self.seq}, {self.role}, turn={self.turn_id}, {len(self.content)}c)"


def transcript(*, turns: int, characters: int) -> list[Row]:
    """A system prompt then `turns` complete turns, each holding `characters` of content."""
    rows = [Row(1, "system", None, "system prompt")]
    seq = 1
    for turn_id in range(1, turns + 1):
        for role in ("user", "assistant", "tool", "assistant"):
            seq += 1
            rows.append(Row(seq, role, turn_id, "x" * (characters // 4)))
    return rows


class TestTheBudgetIsSize:
    def test_a_few_small_turns_are_all_kept(self) -> None:
        """Nothing to do: eight turns of a chatty-but-brief model sit well inside the budget, and
        trimming them would rewrite the cacheable prefix for nothing."""
        rows = transcript(turns=8, characters=2_000)

        plan = plan_compaction(rows, keep_turns=8, tokens_per_character=RATIO)

        kept_turns = {r.turn_id for r in plan.keep if r.turn_id}
        assert len(kept_turns) == 8

    def test_the_same_message_count_is_cut_when_the_messages_are_large(self) -> None:
        """**The bug, stated directly.** Identical shape — four turns of four messages — and one is
        kept whole while the other is cut down, because one is nine times the size. Under a message
        cap both were "twelve messages" and both were kept.
        """
        small = plan_compaction(transcript(turns=4, characters=2_000), tokens_per_character=RATIO)
        large = plan_compaction(transcript(turns=4, characters=60_000), tokens_per_character=RATIO)

        assert len({r.turn_id for r in small.keep if r.turn_id}) == 4
        assert len({r.turn_id for r in large.keep if r.turn_id}) < 4

    def test_what_is_kept_lands_under_the_budget(self) -> None:
        rows = transcript(turns=6, characters=40_000)

        plan = plan_compaction(rows, keep_turns=6, tokens_per_character=RATIO)

        assert sent_characters(plan.keep) * RATIO <= KEEP_TAIL_TOKENS

    def test_it_never_keeps_less_than_one_turn(self) -> None:
        """A turn stripped of its own context has nothing to act on, so one is the floor whatever
        the budget says."""
        rows = transcript(turns=3, characters=400_000)

        plan = plan_compaction(rows, keep_turns=3, tokens_per_character=RATIO)

        assert len({r.turn_id for r in plan.keep if r.turn_id}) == 1

    def test_without_a_ratio_it_falls_back_to_counting_messages(self) -> None:
        """A seat that has never been measured has no ratio to apportion with, and inventing one
        would be the estimate AGENT-19 refuses. The old ceiling still holds the shape."""
        rows = transcript(turns=6, characters=40_000)

        plan = plan_compaction(rows, keep_turns=6, max_kept_messages=12, tokens_per_character=0.0)

        assert len(plan.keep) <= 13, "the system prompt plus at most twelve messages"


class TestClampingTheFloorTurn:
    """When the one turn we must keep is itself over budget, there is no legal cut left: dropping
    to zero turns leaves nothing to act on, and cutting inside a turn orphans a tool result from
    the call that asked for it. Clamping is the only route that honours the budget."""

    def test_an_oversized_floor_turn_is_clamped(self) -> None:
        rows = transcript(turns=2, characters=400_000)

        plan = plan_compaction(rows, keep_turns=2, tokens_per_character=RATIO)

        assert plan.clamp, "the floor turn was kept whole and the budget was not honoured"
        assert all(len(r.content) > CLAMP_THRESHOLD_CHARACTERS for r in plan.clamp)

    def test_a_turn_inside_the_budget_is_never_clamped(self) -> None:
        """Clamping rewrites the cacheable prefix, so it is spent only where it buys something."""
        rows = transcript(turns=2, characters=2_000)

        plan = plan_compaction(rows, keep_turns=2, tokens_per_character=RATIO)

        assert plan.clamp == []

    def test_clamping_makes_a_pass_worthwhile_on_its_own(self) -> None:
        """The deadlock was a pass with nothing to fold and nothing to trim reporting failure and
        giving up. A clamp is a real change to the request and counts as one."""
        rows = transcript(turns=1, characters=400_000)

        plan = plan_compaction(rows, keep_turns=1, tokens_per_character=RATIO)

        assert plan.fold == [] and plan.trim == []
        assert plan.clamp and plan.worthwhile


class TestWhatAClampedMessageLooksLike:
    def test_it_keeps_the_head_and_the_tail(self) -> None:
        """A reasoning block opens with what it is considering and closes with what it decided; the
        enumeration between is the part the board can answer for (invariant 1)."""
        content = "OPENING" + ("x" * 200_000) + "CONCLUSION"

        rendered = clamped_content(content)

        assert rendered.startswith("OPENING")
        assert rendered.endswith("CONCLUSION")
        assert len(rendered) < len(content) / 10

    def test_it_says_how_much_went(self) -> None:
        """A model reading its own reasoning with a silent hole in it cannot tell a gap from a
        thought it never had."""
        rendered = clamped_content("x" * 100_000)

        assert "dropped to save context" in rendered
        assert f"{100_000 - CLAMP_HEAD_CHARACTERS - CLAMP_TAIL_CHARACTERS:,}" in rendered

    def test_it_is_deterministic(self) -> None:
        """Derived from `content` on every render rather than stored pre-cut, so the row serialises
        identically each time and the cacheable prefix does not move (invariant 2, ADR-0003)."""
        content = "y" * 300_000

        assert clamped_content(content) == clamped_content(content)

    def test_a_message_that_would_not_shrink_is_left_alone(self) -> None:
        short = "z" * (CLAMP_HEAD_CHARACTERS + CLAMP_TAIL_CHARACTERS - 1)

        assert clamped_content(short) == short


def test_the_budget_matches_what_other_harnesses_protect() -> None:
    """Asserted because it is a policy, not an implementation detail: oh-my-pi's `keepRecentTokens`
    and Hermes' token-budget tail protection both default to 20,000, and Pydantic AI offers
    `keep_tokens` for the same reason. Ours is the same number for the same argument."""
    assert KEEP_TAIL_TOKENS == 20_000


@pytest.mark.parametrize("characters", [0, -1])
def test_a_nonsense_ratio_is_ignored(characters: int) -> None:
    """A ratio of zero means "never measured", and a negative one can only be a bug. Either way the
    message ceiling decides rather than arithmetic on a number nobody produced."""
    rows = transcript(turns=6, characters=40_000)

    plan = plan_compaction(rows, keep_turns=6, tokens_per_character=float(characters))

    assert len(plan.keep) <= 13

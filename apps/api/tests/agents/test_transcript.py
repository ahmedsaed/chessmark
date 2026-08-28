"""The agent sees the whole game, every turn — and the prefix stays byte-identical.

This is the most consequential test in the phase. Invariant 2 / ADR-0003 says the message list is
append-only and byte-stable so that provider prompt caching applies; without it a 60-move game
costs O(n²) prompt tokens and the product is unaffordable.

The check is deliberately byte-wise on the serialised JSON, not a structural comparison. A
timestamp, a re-ordered key, or a re-rendered system prompt would all pass a loose check and all
silently drop the cache hit rate to zero.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import prompts, transcript
from chessmark.agents.scripted import plays, says, scripted, step, tool_call
from chessmark.db.models import TranscriptMessage
from chessmark.game import Colour
from tests.agents.conftest import Table, play_turn, seat

pytestmark = pytest.mark.integration


def _serialise(messages: list[dict[str, object]]) -> list[str]:
    """Each message as its own exact byte string.

    Per-message rather than one blob for the whole list: serialising the list would wrap it in
    `[...]`, and the closing bracket would break a prefix comparison for reasons that have nothing
    to do with the property under test. Comparing element-wise is also stricter — it catches a
    single re-ordered key inside any one message.
    """
    return [json.dumps(message, sort_keys=False, separators=(",", ":")) for message in messages]


# ====================================================================== the core property


async def test_a_later_turn_is_a_byte_exact_extension_of_the_earlier_one(
    db: AsyncSession, table: Table
) -> None:
    """Exit criterion: turn N+1's message list starts with turn N's, byte for byte."""
    model = plays(["e4", "d4"])

    await play_turn(db, table, model, colour=Colour.WHITE)
    after_first = _serialise(await transcript.build_messages(db, table.white.id))

    await play_turn(db, table, plays(["e5"]), colour=Colour.BLACK)
    await play_turn(db, table, model, colour=Colour.WHITE)
    after_second = _serialise(await transcript.build_messages(db, table.white.id))

    assert after_second[: len(after_first)] == after_first, (
        "turn 2 rewrote history — prompt caching is now worthless and cost is quadratic"
    )
    assert len(after_second) > len(after_first), "turn 2 appended nothing"


async def test_the_property_holds_across_many_turns(db: AsyncSession, table: Table) -> None:
    """One extension could be luck. Six in a row is the mechanism working."""
    white = plays(["e4", "Nf3", "Bc4", "d3", "O-O", "Nc3"])
    black = plays(["e5", "Nc6", "Bc5", "d6", "Nf6", "O-O"])

    snapshots: list[str] = []
    for index in range(6):
        await play_turn(db, table, white, colour=Colour.WHITE)
        snapshots.append(_serialise(await transcript.build_messages(db, table.white.id)))
        if index < 5:
            await play_turn(db, table, black, colour=Colour.BLACK)

    for earlier, later in itertools.pairwise(snapshots):
        assert later[: len(earlier)] == earlier

    assert len(snapshots[-1]) > len(snapshots[0]) * 3, "the transcript should be growing"


async def test_rebuilding_the_transcript_twice_gives_the_same_bytes(
    db: AsyncSession, table: Table
) -> None:
    """The worker rebuilds from Postgres every turn (ADR-0007); it must be deterministic."""
    await play_turn(db, table, plays(["e4"]), colour=Colour.WHITE)

    first = _serialise(await transcript.build_messages(db, table.white.id))
    db.expunge_all()
    second = _serialise(await transcript.build_messages(db, table.white.id))

    assert first == second


async def test_the_system_prompt_is_written_once_and_never_changes(
    db: AsyncSession, table: Table
) -> None:
    """It heads the cached prefix, so re-rendering it would invalidate the entire game."""
    white = plays(["e4", "d4"])

    await play_turn(db, table, white, colour=Colour.WHITE)
    first_system = (await transcript.build_messages(db, table.white.id))[0]

    await play_turn(db, table, plays(["e5"]), colour=Colour.BLACK)
    await play_turn(db, table, white, colour=Colour.WHITE)
    second_system = (await transcript.build_messages(db, table.white.id))[0]

    assert first_system == second_system
    assert first_system["role"] == "system"

    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TranscriptMessage)
        .where(
            TranscriptMessage.player_id == table.white.id,
            TranscriptMessage.role == "system",
        )
    )
    assert count == 1


async def test_the_system_prompt_contains_nothing_that_changes_per_turn(
    db: AsyncSession, table: Table
) -> None:
    """The guard against the classic mistake: interpolating the move number into the prompt."""
    prompt = prompts.build_system_prompt(
        colour=Colour.WHITE,
        opponent="black-model",
        max_illegal_retries=5,
        trash_talk_enabled=True,
    )

    # A FEN, a ply counter, or a move list here would break caching for every subsequent turn.
    #
    # **Word boundaries, not substrings.** The first version matched `"ply "`, which is inside
    # `"apply "` — so documenting the draw rules tripped a guard about move counters. A guard that
    # fires on unrelated prose gets loosened by whoever hits it next, and this one is protecting
    # invariant 2, so it is worth stating precisely instead.
    forbidden = {
        r"\bply\s+\d": "a ply number",
        r"\bmove\s+number\b": "a move number",
        r"\b[rnbqkpRNBQKP1-8]{8}/[rnbqkpRNBQKP1-8/]{7,}": "a FEN",
        r"\bfen\s*[:=]": "a FEN",
        r"^\s*1\.\s": "a move list",
        r"\bcurrently\b|\bso far\b|\bright now\b": "per-turn state",
    }
    for pattern, what in forbidden.items():
        assert not re.search(pattern, prompt, re.MULTILINE), (
            f"the system prompt contains {what} ({pattern!r}), which changes per turn"
        )

    # And the positive half: whatever it says, it must be identical for two different turns of the
    # same game. The only inputs are fixed for the game, so this is a check on that staying true.
    assert prompt == prompts.build_system_prompt(
        colour=Colour.WHITE,
        opponent="black-model",
        max_illegal_retries=5,
        trash_talk_enabled=True,
    )


# ====================================================================== structure


async def test_the_transcript_starts_with_the_system_prompt(db: AsyncSession, table: Table) -> None:
    await play_turn(db, table, plays(["e4"]), colour=Colour.WHITE)

    messages = await transcript.build_messages(db, table.white.id)

    assert messages[0]["role"] == "system"
    assert "white" in str(messages[0]["content"]).lower()
    assert "black-model" in str(messages[0]["content"])


async def test_each_player_has_an_independent_transcript(db: AsyncSession, table: Table) -> None:
    await play_turn(db, table, plays(["e4"]), colour=Colour.WHITE)
    await play_turn(db, table, plays(["e5"]), colour=Colour.BLACK)

    white = await transcript.build_messages(db, table.white.id)
    black = await transcript.build_messages(db, table.black.id)

    assert "playing a game of chess as white" in str(white[0]["content"]).lower()
    assert "playing a game of chess as black" in str(black[0]["content"]).lower()
    assert white != black


async def test_tool_results_are_paired_with_their_calls(db: AsyncSession, table: Table) -> None:
    """Providers reject a transcript with an unanswered tool call, so every id needs a result."""
    await play_turn(
        db,
        table,
        scripted(
            step(
                tool_call("get_board", call_id="a"),
                tool_call("get_legal_moves", call_id="b"),
            ),
            step(tool_call("make_move", call_id="c", move="e4")),
        ),
        colour=Colour.WHITE,
    )

    messages = await transcript.build_messages(db, table.white.id)

    requested = {
        call["id"]
        for message in messages
        if message.get("tool_calls")
        for call in message["tool_calls"]  # type: ignore[union-attr]
    }
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}

    assert requested == {"a", "b", "c"}
    assert answered == requested


async def test_a_tool_message_carries_the_id_and_name(db: AsyncSession, table: Table) -> None:
    await play_turn(db, table, plays(["e4"]), colour=Colour.WHITE)

    tool_messages = [
        m for m in await transcript.build_messages(db, table.white.id) if m["role"] == "tool"
    ]

    assert tool_messages
    for message in tool_messages:
        assert message["tool_call_id"]
        assert message["name"]
        assert isinstance(message["content"], str)


async def test_the_turn_prompt_is_appended_each_turn(db: AsyncSession, table: Table) -> None:
    """The per-turn state lives here, in the body — not in the cached system prompt."""
    white = plays(["e4", "d4"])
    await play_turn(db, table, white, colour=Colour.WHITE)
    await play_turn(db, table, plays(["e5"]), colour=Colour.BLACK)
    await play_turn(db, table, white, colour=Colour.WHITE)

    messages = await transcript.build_messages(db, table.white.id)
    turn_prompts = [
        m for m in messages if m["role"] == "user" and "Ply" in str(m.get("content", ""))
    ]

    assert len(turn_prompts) == 2
    assert "Ply 1" in str(turn_prompts[0]["content"])
    assert "Ply 3" in str(turn_prompts[1]["content"])


async def test_sequence_numbers_are_gap_free(db: AsyncSession, table: Table) -> None:
    await play_turn(db, table, plays(["e4"]), colour=Colour.WHITE)

    seqs = (
        await db.scalars(
            sa.select(TranscriptMessage.seq)
            .where(TranscriptMessage.player_id == table.white.id)
            .order_by(TranscriptMessage.seq)
        )
    ).all()

    assert list(seqs) == list(range(1, len(seqs) + 1))


# ====================================================================== rendering


def test_a_tool_row_renders_with_the_keys_providers_expect() -> None:
    row = TranscriptMessage(
        seq=1, role="tool", tool_call_id="call_1", name="get_board", content="{}"
    )
    assert transcript.to_provider_message(row) == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "get_board",
        "content": "{}",
    }


def test_an_assistant_row_with_tool_calls_keeps_a_content_key() -> None:
    """Some providers reject an assistant tool-call message with no `content` key at all."""
    calls = [{"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]
    row = TranscriptMessage(seq=1, role="assistant", content=None, tool_calls=calls)

    rendered = transcript.to_provider_message(row)

    assert rendered["role"] == "assistant"
    assert rendered["tool_calls"] == calls
    assert "content" in rendered


def test_a_plain_assistant_row_has_no_tool_calls_key() -> None:
    row = TranscriptMessage(seq=1, role="assistant", content="thinking")
    assert transcript.to_provider_message(row) == {"role": "assistant", "content": "thinking"}


async def test_appending_to_a_missing_player_raises(db: AsyncSession, table: Table) -> None:
    import uuid

    with pytest.raises(transcript.TranscriptError):
        await transcript.append_message(
            db, player_id=uuid.uuid4(), game_id=table.game.id, role="user", content="hi"
        )


async def test_a_ranked_game_says_talking_is_disabled(db: AsyncSession) -> None:
    ranked = await seat(db, trash_talk_enabled=False)
    await play_turn(db, ranked, plays(["e4"]), colour=Colour.WHITE)

    system = (await transcript.build_messages(db, ranked.white.id))[0]
    assert "ranked" in str(system["content"]).lower()


# ====================================================================== hearing each other


async def test_the_opponent_receives_what_was_said(db: AsyncSession, table: Table) -> None:
    """TALK-02. Without delivery, `say` broadcasts into a void and no exchange is possible."""
    await play_turn(
        db,
        table,
        scripted(says("Your opening is a museum piece.", tool_call("make_move", move="e4"))),
        colour=Colour.WHITE,
    )

    black = await transcript.build_messages(db, table.black.id)
    heard = [m for m in black if "Your opponent says" in str(m.get("content", ""))]

    assert len(heard) == 1
    assert "museum piece" in str(heard[0]["content"])
    assert heard[0]["role"] == "user"


async def test_a_speaker_does_not_hear_itself(db: AsyncSession, table: Table) -> None:
    await play_turn(
        db,
        table,
        scripted(says("Watch this.", tool_call("make_move", move="e4"))),
        colour=Colour.WHITE,
    )

    white = await transcript.build_messages(db, table.white.id)
    assert not [m for m in white if "Your opponent says" in str(m.get("content", ""))]


async def test_models_can_hold_a_conversation(db: AsyncSession, table: Table) -> None:
    """The point of a standalone `say` tool (ADR-0009): a genuine back-and-forth."""
    await play_turn(
        db,
        table,
        scripted(says("I open strong.", tool_call("make_move", move="e4"))),
        colour=Colour.WHITE,
    )
    await play_turn(
        db,
        table,
        scripted(says("You open predictably.", tool_call("make_move", move="e5"))),
        colour=Colour.BLACK,
    )
    await play_turn(
        db,
        table,
        scripted(says("Predictable wins games.", tool_call("make_move", move="Nf3"))),
        colour=Colour.WHITE,
    )

    white = await transcript.build_messages(db, table.white.id)
    black = await transcript.build_messages(db, table.black.id)

    assert "You open predictably." in str(white)
    assert "I open strong." in str(black)
    assert "Predictable wins games." in str(black)


async def test_a_taunt_arrives_before_the_opponents_next_turn_prompt(
    db: AsyncSession, table: Table
) -> None:
    """Ordering matters: the model must read the taunt as part of the turn it responds to."""
    await play_turn(
        db,
        table,
        scripted(says("Beat that.", tool_call("make_move", move="e4"))),
        colour=Colour.WHITE,
    )
    await play_turn(db, table, plays(["e5"]), colour=Colour.BLACK)

    black = await transcript.build_messages(db, table.black.id)
    contents = [str(m.get("content", "")) for m in black]

    heard_at = next(i for i, c in enumerate(contents) if "Beat that." in c)
    prompted_at = next(i for i, c in enumerate(contents) if "Ply 2" in c)

    assert heard_at < prompted_at


async def test_an_opening_taunt_does_not_displace_the_system_prompt(
    db: AsyncSession, table: Table
) -> None:
    """Black has never played, so its transcript is empty — the taunt must not become row 1."""
    await play_turn(
        db,
        table,
        scripted(says("First blood.", tool_call("make_move", move="e4"))),
        colour=Colour.WHITE,
    )

    black = await transcript.build_messages(db, table.black.id)

    assert black[0]["role"] == "system"
    assert "playing a game of chess as black" in str(black[0]["content"]).lower()
    assert "First blood." in str(black[1]["content"])


async def test_delivery_preserves_the_prefix_property(db: AsyncSession, table: Table) -> None:
    """Appending to an idle transcript must not disturb what the opponent already saw."""
    black = plays(["e5", "Nc6"])

    await play_turn(db, table, plays(["e4"]), colour=Colour.WHITE)
    await play_turn(db, table, black, colour=Colour.BLACK)
    before = _serialise(await transcript.build_messages(db, table.black.id))

    await play_turn(
        db,
        table,
        scripted(says("Still losing.", tool_call("make_move", move="Nf3"))),
        colour=Colour.WHITE,
    )
    await play_turn(db, table, black, colour=Colour.BLACK)
    after = _serialise(await transcript.build_messages(db, table.black.id))

    assert after[: len(before)] == before
    assert any("Still losing." in message for message in after)


async def test_nothing_is_delivered_in_a_ranked_game(db: AsyncSession) -> None:
    ranked = await seat(db, trash_talk_enabled=False)

    await play_turn(
        db,
        ranked,
        scripted(
            step(tool_call("say", message="trash")),
            step(tool_call("make_move", move="e4")),
        ),
        colour=Colour.WHITE,
    )

    black = await transcript.build_messages(db, ranked.black.id)
    assert not [m for m in black if "Your opponent says" in str(m.get("content", ""))]

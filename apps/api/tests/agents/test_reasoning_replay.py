"""Reasoning has to be handed back, not just recorded.

Several models treat their own prior reasoning as part of the history they require. Gemini 3
refuses a function call whose `thought_signature` is missing; DeepSeek refuses a thinking-mode
history without `reasoning_content`. OpenRouter normalises both into `reasoning_details` and is
explicit that the sequence must be replayed **unmodified**.

Chessmark dropped the field for nine phases, and it went unnoticed because the models we happened
to play tolerated it. `deepseek-v4-pro` did not: without its own reasoning it stopped emitting
structured tool calls and started writing raw DSML tool-call markup into its reasoning
instead, then forfeited the game for "replying without calling a tool".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents import transcript
from chessmark.agents.normalise import extract_reasoning_details, normalise_response
from chessmark.agents.scripted import step, tool_call
from chessmark.db.models import TranscriptMessage
from tests.orchestration.conftest import Fixture, run_next

# A Gemini-shaped block: the signature is opaque and load-bearing.
GEMINI_BLOCK: dict[str, Any] = {
    "type": "reasoning.text",
    "text": "Considering the Sicilian.",
    "signature": "EroBCkYIBxgCKkCsigned-opaque-blob",
    "index": 0,
}
DEEPSEEK_BLOCK: dict[str, Any] = {
    "type": "reasoning.text",
    "text": "White played e4. I will reply c5.",
    "index": 0,
}


# ====================================================================== extraction


def test_reasoning_details_are_extracted_verbatim() -> None:
    """Opaque on purpose. Reading or reshaping the blocks is how the signatures stop matching."""
    details = extract_reasoning_details({"reasoning_details": [GEMINI_BLOCK]})

    assert details == [GEMINI_BLOCK]
    assert details is not None
    assert details[0]["signature"] == GEMINI_BLOCK["signature"]


def test_a_response_without_reasoning_details_yields_none() -> None:
    """Most models send none, and an empty list must not become an empty field on the way back."""
    assert extract_reasoning_details({}) is None
    assert extract_reasoning_details({"reasoning_details": []}) is None
    assert extract_reasoning_details({"reasoning_details": "not a list"}) is None


def test_normalise_response_carries_reasoning_details() -> None:
    payload = step(tool_call("make_move", move="e4"))
    payload["choices"][0]["message"]["reasoning_details"] = [DEEPSEEK_BLOCK]

    parsed = normalise_response(payload)

    assert parsed.reasoning_details == [DEEPSEEK_BLOCK]


# ====================================================================== replay


def test_an_assistant_message_replays_its_reasoning() -> None:
    row = TranscriptMessage(
        role="assistant",
        content=None,
        tool_calls=[{"id": "1", "type": "function"}],
        reasoning_details=[GEMINI_BLOCK],
    )

    message = transcript.to_provider_message(row)

    assert message["reasoning_details"] == [GEMINI_BLOCK]


def test_a_message_without_reasoning_omits_the_field_entirely() -> None:
    """An empty `reasoning_details` is not the same as no field, and sending one to a model that
    did not produce it changes a request for no reason."""
    row = TranscriptMessage(role="user", content="It is your move.", tool_calls=None)

    assert "reasoning_details" not in transcript.to_provider_message(row)


def test_user_and_tool_messages_never_carry_reasoning() -> None:
    tool_row = TranscriptMessage(
        role="tool", tool_call_id="1", name="make_move", content='{"ok": true}'
    )

    assert "reasoning_details" not in transcript.to_provider_message(tool_row)


def test_the_assistant_message_helper_accepts_reasoning() -> None:
    built = transcript.assistant_message(
        content=None, tool_calls=None, reasoning_details=[DEEPSEEK_BLOCK]
    )

    assert built["reasoning_details"] == [DEEPSEEK_BLOCK]


# ====================================================================== end to end


@pytest.mark.integration
async def test_a_turn_stores_and_replays_the_models_reasoning(
    db: AsyncSession, game: Fixture, make_worker: Any
) -> None:
    """The bug in full: a turn's reasoning must survive into the *next* turn's request, because
    that is the request the model validates."""

    async def completion_fn(**kwargs: Any) -> dict[str, Any]:
        payload = step(tool_call("make_move", move="e4"))
        payload["choices"][0]["message"]["reasoning_details"] = [GEMINI_BLOCK]
        return payload

    worker = make_worker(completion_fn)
    await run_next(worker, game.queue)

    db.expunge_all()
    stored = (
        await db.scalars(
            sa.select(TranscriptMessage)
            .where(TranscriptMessage.role == "assistant")
            .order_by(TranscriptMessage.seq)
        )
    ).all()

    assert stored, "no assistant message was recorded"
    assert stored[0].reasoning_details == [GEMINI_BLOCK]

    replayed = transcript.to_provider_message(stored[0])
    assert replayed["reasoning_details"] == [GEMINI_BLOCK], (
        "the model's own reasoning did not make it back into the replayed history"
    )


@pytest.mark.integration
async def test_a_transcript_written_before_the_column_existed_still_replays(
    db: AsyncSession, game: Fixture
) -> None:
    """The column is nullable so old games stay readable. Those rows replay exactly as they were
    sent at the time, which is the honest thing for a record of what happened."""
    player_id = game.white.id
    await transcript.append_message(
        db,
        player_id=player_id,
        game_id=game.game.id,
        role="assistant",
        content="an older turn",
    )
    await db.flush()

    row = await db.scalar(
        sa.select(TranscriptMessage).where(TranscriptMessage.player_id == player_id)
    )

    assert row is not None
    assert row.reasoning_details is None
    assert "reasoning_details" not in transcript.to_provider_message(row)
    assert isinstance(player_id, uuid.UUID)


def test_reasoning_details_are_found_under_provider_specific_fields() -> None:
    """Where they actually live on a real call.

    LiteLLM does not hand OpenRouter's response through untouched — it re-normalises into OpenAI's
    shape and files anything non-standard under `provider_specific_fields`. The first version of
    this fix read only the top level, passed every unit test written against a hand-made payload,
    and did nothing whatsoever in production.
    """
    message = {
        "role": "assistant",
        "provider_specific_fields": {"refusal": None, "reasoning_details": [DEEPSEEK_BLOCK]},
    }

    assert extract_reasoning_details(message) == [DEEPSEEK_BLOCK]


def test_the_top_level_wins_when_both_are_present() -> None:
    """If a future LiteLLM starts passing it through, prefer the standard location."""
    message = {
        "reasoning_details": [GEMINI_BLOCK],
        "provider_specific_fields": {"reasoning_details": [DEEPSEEK_BLOCK]},
    }

    assert extract_reasoning_details(message) == [GEMINI_BLOCK]


def test_a_provider_specific_field_that_is_not_a_dict_is_ignored() -> None:
    assert extract_reasoning_details({"provider_specific_fields": "nope"}) is None
    assert extract_reasoning_details({"provider_specific_fields": None}) is None

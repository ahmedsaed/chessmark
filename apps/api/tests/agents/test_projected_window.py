"""The window is sized for the request in front of us, not the one behind it (ADR-0039).

Game `ed491262` was abandoned at ply 43 on a 400 nobody had to guess about:

    The request is 290310 tokens long and exceeds this model's context length of 262144 tokens.

The endpoint's window was in our registry, correct, at 262,144. The seat's last measurement was
195,503 — 40,427 tokens below the 235,930 that triggers a fold — so compaction did not run, and
`completion_cap` cleared 62,545 tokens of output against the room that measurement implied. By then
the transcript had grown to 227,765. It had added 32,262 tokens inside one turn and the guard band
is 26,214 wide, so the seat stepped clean over it. It never compacted once in 43 plies.

The growth was never invisible: `_sent_characters` counts the rows going out, on the line above the
decision, and `players.last_prompt_characters` holds the character count the measurement belongs
to. Their quotient is the endpoint's own rate for this transcript. It was used to size the retained
tail and nothing else.

So the rule here is a property, not a threshold: **the figure both decisions read tracks the
transcript, and may only ever be larger than the measurement it came from.**
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.compaction import FRAMING_TOKENS
from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.db.models import LlmCall, ModelEndpoint, ModelRegistry
from chessmark.game import Colour
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration

#: ~730,000 characters, standing in for the ~27,000 tokens of its own reasoning that
#: `ed491262`'s seat replayed every single turn.
#:
#: **Large enough to dominate the transcript**, which is what keeps the test from being a tripwire
#: on the system prompt's length. An earlier version let the rate emerge from a scripted token
#: count over whatever the opening request happened to weigh, and the projection landed at 128,073
#: against a 131,904 floor — so ADR-0040 lengthening the prompt by a few hundred characters, which
#: has nothing to do with any of this, failed it. The rate is pinned below and the ballast is most
#: of what it converts, so a prompt edit moves the result by a few hundred tokens rather than
#: across the assertion.
BALLAST = "position " * 81_000


async def _register(db: AsyncSession, *, slug: str, context: int) -> None:
    await sync_model_registry(
        db, [{"openrouter_id": slug, "display_name": slug, "context_length": context}]
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


async def _asked(db: AsyncSession) -> list[int | None]:
    calls = list(await db.scalars(sa.select(LlmCall).order_by(LlmCall.id)))
    return [call.request.get("max_tokens") for call in calls]


async def test_the_output_bound_follows_a_transcript_that_grew(
    db: AsyncSession, table: Table
) -> None:
    """The failure, in miniature: measure, append a great deal, then ask for output.

    Without the projection the last call is sized against 1,000 tokens, because that is what the
    provider said about a request that no longer resembles the one going out — and it asks for the
    whole 64,000. That is the 62,545 tokens `ed491262` was cleared to generate against a prompt
    with half that room.
    """
    slug = "scripted/grew"
    await _register(db, slug=slug, context=200_000)

    # A turn that leaves a great deal behind it, exactly as a reasoning model replaying 27,000
    # tokens of its own thinking does every turn.
    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), content=BALLAST)),
        model=slug,
        colour=Colour.WHITE,
    )
    await play_turn(
        db, table, scripted(step(tool_call("make_move", move="e5"))), colour=Colour.BLACK
    )

    # **The rate, stated rather than inferred.** 1,000 tokens for 5,000 characters is the ~0.2
    # tokens per character a real chess transcript runs at; pinning it here is what makes the
    # assertion about the projection instead of about how long the system prompt happens to be.
    table.white.last_prompt_tokens = 1_000
    table.white.last_prompt_characters = 5_000
    await db.commit()

    before = len(await _asked(db))
    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="d4"))),
        model=slug,
        colour=Colour.WHITE,
    )

    asked = (await _asked(db))[before]
    assert asked is not None and asked < 64_000, (
        "the transcript in front of this call is ~730,000 characters. Sized against the 1,000 "
        "tokens last measured it would ask for the full 64,000 — reading the previous request's "
        "count is the whole of ADR-0039's bug"
    )

    # What the bound implies we think the prompt now is, read back out of the arithmetic.
    projected = 200_000 - FRAMING_TOKENS - asked
    assert 120_000 < projected < 180_000, (
        f"{projected} tokens for ~730,000 characters at the seat's own measured rate — a figure "
        "outside this band means the projection is reading a token count and a character count "
        "that do not describe one transcript"
    )


async def test_the_figure_never_drops_below_what_was_measured(
    db: AsyncSession, table: Table
) -> None:
    """`max` against the measurement, so a low ratio cannot reproduce the failure.

    The dangerous direction is closed by construction rather than by trusting the quotient: the
    worst a drifting rate can do is fold a turn early or ask for less output. It also covers the
    case the ratio genuinely cannot describe — straight after a fold the stored token figure is a
    *bound* on the pre-fold size while the characters are post-fold, and scaling one by the other
    would be inventing room.
    """
    slug = "scripted/shrank"
    await _register(db, slug=slug, context=200_000)

    # A measurement paired with far more characters than the next request will carry, which is the
    # shape that would scale the figure *down* if anything were allowed to. Left unscaled it leaves
    # less room than we ask for, so the bound is visible in what goes out.
    table.white.last_prompt_tokens = 140_000
    table.white.last_prompt_characters = 10_000_000
    await db.commit()

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=140_000)),
        model=slug,
        colour=Colour.WHITE,
    )

    assert (await _asked(db))[0] == 200_000 - 140_000 - FRAMING_TOKENS, (
        "the measurement stands. Scaled by the stored pair this prompt would come to a few dozen "
        "tokens and the call would ask for the full 64,000 — nothing may quietly decide there is "
        "more room than the provider's own last count implied"
    )


async def test_an_unmeasured_seat_is_still_unmeasured(db: AsyncSession, table: Table) -> None:
    """A rate with no measurement under it is precisely what ADR-0021 deleted.

    The first call of a game has nothing to project from, and the honest answer there is the
    reserve — not a number derived from characters alone.
    """
    slug = "scripted/fresh"
    await _register(db, slug=slug, context=200_000)

    await play_turn(
        db,
        table,
        scripted(step(tool_call("make_move", move="e4"), prompt_tokens=1_000)),
        model=slug,
        colour=Colour.WHITE,
    )

    assert (await _asked(db))[0] == 20_000, "the reserve, held back rather than guessed at"

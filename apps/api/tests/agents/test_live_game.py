"""One real model, ten real plies.

**Marked `llm` and deselected by default** — it calls a provider and spends free-tier requests.
Run deliberately:

    make test-llm

The whole rest of the suite replays fixtures. This exists so that "the turn loop works against
scripted responses" and "the turn loop works against an actual model" stay separate claims. Real
models produce shapes a script never will: an unexpected tool, a stray prose reply, a genuinely
illegal move.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.llm import LlmGateway
from chessmark.agents.turn import TurnLimits, TurnRunner, ensure_system_prompt
from chessmark.db.enums import TurnStatus
from chessmark.db.models import LlmCall, Ply
from chessmark.game import Colour
from tests.agents.conftest import Table, seat

pytestmark = [pytest.mark.llm, pytest.mark.integration]

#: Override with CHESSMARK_LIVE_MODEL to try another. Not all free models can sustain a game:
#: `openai/gpt-oss-20b:free` plays reasonable chess for a few plies, then spirals into tens of
#: thousands of reasoning tokens per move and stops finishing turns.
#:
#: **A pinned free slug is a perishable thing.** The previous default,
#: `nvidia/nemotron-nano-9b-v2:free`, was delisted and began answering
#: `404 No endpoints found`, so this test failed at ply 0 on a stale pin rather than on anything
#: it exists to measure — and it fails hard, because the ply assertion has no escape hatch by
#: design. `poolside/laguna-s-2.1:free` replaces it because this repository already has evidence
#: it can play: 22 plies of the Giuoco Piano, both sides castled, zero illegal attempts.
#:
#: When it goes the same way, the fix is one line here. `make models-free` lists what is currently
#: served and tool-capable.
MODEL = os.environ.get("CHESSMARK_LIVE_MODEL", "poolside/laguna-s-2.1:free")
TARGET_PLIES = 10


@pytest.fixture
def requires_api_key() -> None:
    """Skip unless a key is present — **without ever returning it**.

    pytest prints the value of every fixture argument in a failure traceback, so a fixture that
    returned the key would put it in plaintext in the terminal, in the log file, and in any CI
    artifact the moment a test failed. It did exactly that once. The key is read inside the test
    instead, where it stays a local of a frame pytest does not dump.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY is not set")


#: Free models are slow, so the wall clock is raised here and only here — otherwise this test
#: would measure provider latency rather than the turn loop. The token cap is left at the
#: production default on purpose: unbounded reasoning is exactly the failure this test found.
#: A real model on a free endpoint is slow, and the bound that matters is now per *call*, in the
#: gateway (`LlmGateway(timeout=...)`), not per turn. The default 600s already covers the worst call
#: observed (442s), so there is nothing left to raise here.
LIVE_LIMITS = TurnLimits()


async def test_a_real_model_plays_ten_plies(db: AsyncSession, requires_api_key: None) -> None:
    """Exit criterion: one real cheap model plays 10 plies without a crash."""
    table: Table = await seat(db)
    gateway = LlmGateway(api_key=os.environ["OPENROUTER_API_KEY"])

    for colour in (Colour.WHITE, Colour.BLACK):
        await ensure_system_prompt(
            db,
            game=table.game,
            player=table.player(colour),
            opponent_name=table.opponent_name(colour),
        )
    await db.commit()

    illegal_total = 0

    for ply in range(1, TARGET_PLIES + 1):
        colour = table.referee.side_to_move
        runner = TurnRunner(
            db,
            gateway=gateway,
            referee=table.referee,
            game=table.game,
            player=table.player(colour),
            opponent=table.player(colour.opponent),
            model=MODEL,
            limits=LIVE_LIMITS,
        )
        result = await runner.run()
        await db.commit()

        illegal_total += result.illegal_attempts
        print(
            f"ply {ply:>2} {colour.value:<5} "
            f"{result.move.move.san if result.move else '—':<7} "
            f"illegal={result.illegal_attempts} tokens={result.prompt_tokens}"
            f"+{result.completion_tokens} cached={result.cached_tokens} "
            f"{result.latency_ms}ms  {result.status.value}"
        )

        # No escape hatch. A forfeit here is a real failure to meet the criterion, and letting
        # `referee.is_over` excuse it would make this test pass while proving nothing — a game
        # cannot legitimately end in a chess result inside ten plies.
        # A failure at ply 1 is usually not a finding about the model. `MODEL` is a pinned free
        # slug, and free slugs are withdrawn without notice — so name that possibility here rather
        # than leaving the next person to read a 404 as a benchmark result.
        hint = (
            f"\n\n{MODEL} may no longer be served — a delisted free model answers "
            "'404 No endpoints found'. Check `make models-free` and update MODEL, or set "
            "CHESSMARK_LIVE_MODEL."
            if ply == 1
            else ""
        )
        assert result.status is TurnStatus.COMPLETED, (
            f"ply {ply} ended as {result.status.value}: "
            f"{result.error or (result.outcome.detail if result.outcome else '?')}{hint}"
        )
        assert result.moved

    plies = (
        await db.scalars(
            sa.select(Ply).where(Ply.game_id == table.game.id).order_by(Ply.ply_number)
        )
    ).all()
    calls = (await db.scalars(sa.select(LlmCall).where(LlmCall.game_id == table.game.id))).all()

    assert len(plies) == TARGET_PLIES, f"only reached ply {len(plies)}"
    assert not table.referee.is_over, "a real game cannot legitimately end inside ten plies"
    assert calls, "nothing was recorded"
    assert all(call.request for call in calls), "requests must be stored verbatim"
    print(f"\nmoves: {' '.join(p.san for p in plies)}")
    print(f"illegal attempts across the game: {illegal_total}")

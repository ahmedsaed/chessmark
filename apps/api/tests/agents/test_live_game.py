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

MODEL = "openai/gpt-oss-20b:free"
TARGET_PLIES = 10


@pytest.fixture
def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        pytest.skip("OPENROUTER_API_KEY is not set")
    return key


#: Free models are *slow* — observed turns of 30s, 120s, and 230s on gpt-oss-20b:free, mostly
#: spent generating reasoning tokens. The production default of 180s would forfeit those turns,
#: which is correct behaviour for a real game but would mean this test measures provider latency
#: instead of the turn loop. Raised here deliberately, and only here.
LIVE_LIMITS = TurnLimits(max_seconds=900.0)


async def test_a_real_model_plays_ten_plies(db: AsyncSession, api_key: str) -> None:
    """Exit criterion: one real cheap model plays 10 plies without a crash."""
    table: Table = await seat(db)
    gateway = LlmGateway(api_key=api_key)

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
        assert result.status is TurnStatus.COMPLETED, (
            f"ply {ply} ended as {result.status.value}: "
            f"{result.error or (result.outcome.detail if result.outcome else '?')}"
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

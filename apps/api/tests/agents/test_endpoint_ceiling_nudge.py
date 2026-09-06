"""An endpoint's own ceiling earns a nudge, however accurately we name it (ADR-0032).

The failure this file exists to prevent happened the moment the registry became *correct*.

Poolside stops `laguna-s-2.1` at 32,768 output tokens. While the catalogue was stale we asked for
64,000, the model produced 32,768, and `32,768 < 64,000` read as *the endpoint's* limit — which
earns "you were cut off, be brief and act" and up to `MAX_TRUNCATIONS` retries. It usually
recovered. Then the catalogue was refreshed, `max_completion` became the true 32,768, we asked for
exactly that, and the identical response read as *ours*: failed on the spot, no nudge, five job
attempts, `a016a326` abandoned at ply 72.

Same model, same behaviour, opposite verdict — purely because we got the number right.

The rule here is the *size* of what we allowed, not whether it was reached. A model handed a usable
budget and told to be brief has every chance of acting; one handed a token has none, and that is
the only case worth failing on sight for.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.compaction import MIN_USEFUL_COMPLETION
from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.turn import MAX_TRUNCATIONS, TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import ModelEndpoint, ModelRegistry
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration

#: Poolside's real ceiling, and the value every one of those truncations came back at.
ENDPOINT_CEILING = 32_768
MODEL = "poolside/laguna-s-2.1:free"


def cut_off(tokens: int) -> dict[str, object]:
    return step(finish_reason="length", completion_tokens=tokens)


async def _register(db: AsyncSession, *, max_completion: int | None) -> None:
    """A model whose single endpoint declares (or does not declare) an output ceiling."""
    await sync_model_registry(
        db, [{"openrouter_id": MODEL, "display_name": MODEL, "context_length": 256_000}]
    )
    await db.flush()
    model_id = await db.scalar(
        sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == MODEL)
    )
    db.add(
        ModelEndpoint(
            model_id=model_id,
            provider_name="TestProvider",
            context_length=256_000,
            max_completion_tokens=max_completion,
            supports_tools=True,
            is_active=True,
        )
    )
    await db.commit()


async def test_a_declared_ceiling_is_nudged_not_failed(db: AsyncSession, table: Table) -> None:
    """**The regression.** The catalogue now knows the endpoint stops at 32,768, so we ask for
    exactly that and the model fills it. A budget that size is perfectly answerable, so the model
    earns the retries that were working before the registry got accurate.
    """
    await _register(db, max_completion=ENDPOINT_CEILING)
    calls = 0

    async def counting(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return cut_off(ENDPOINT_CEILING)

    await play_turn(
        db, table, counting, limits=TurnLimits(max_completion_tokens=64_000), model=MODEL
    )

    assert calls == MAX_TRUNCATIONS + 1, (
        f"the endpoint's own ceiling was treated as ours and failed on sight, after {calls} call(s)"
    )


async def test_and_it_recovers_when_the_model_acts(db: AsyncSession, table: Table) -> None:
    """The point of the nudge: told to be brief, a model often plays on. Failing on sight threw
    that away — `a016a326` had just played plies 68 to 72 perfectly well."""
    await _register(db, max_completion=ENDPOINT_CEILING)

    result = await play_turn(
        db,
        table,
        scripted(cut_off(ENDPOINT_CEILING), step(tool_call("make_move", move="e4"))),
        limits=TurnLimits(max_completion_tokens=64_000),
        model=MODEL,
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.outcome is None


async def test_an_unusable_ask_still_fails_at_once(db: AsyncSession, table: Table) -> None:
    """**The protection this must not remove.** A miscalculated window once asked an endpoint for
    *one* token; every reply came back truncated and a model lost a game at ply 5 (ADR-0021). At or
    below the floor there is genuinely nothing a nudge can fix — no answer fits — so retrying only
    spends calls to be told so again.
    """
    await _register(db, max_completion=ENDPOINT_CEILING)
    calls = 0

    async def counting(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return cut_off(MIN_USEFUL_COMPLETION)

    result = await play_turn(
        db,
        table,
        counting,
        limits=TurnLimits(max_completion_tokens=MIN_USEFUL_COMPLETION),
        model=MODEL,
    )

    assert calls == 1, f"an unanswerable ask was retried into the strike budget, {calls} calls"
    assert result.status is TurnStatus.FAILED
    assert result.outcome is None, "a harness bound is never a finding about a player"


async def test_a_usable_ask_is_nudged_whatever_the_endpoint_declares(
    db: AsyncSession, table: Table
) -> None:
    """The rule is the *size* of what we allowed, not what the endpoint says about itself. An
    endpoint that declares no ceiling is no reason to give up on a model handed a perfectly usable
    budget — it just means we cannot say whose limit was reached, and a nudge costs little."""
    await _register(db, max_completion=None)
    calls = 0

    async def counting(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return cut_off(8_000)

    await play_turn(
        db, table, counting, limits=TurnLimits(max_completion_tokens=8_000), model=MODEL
    )

    assert calls == MAX_TRUNCATIONS + 1

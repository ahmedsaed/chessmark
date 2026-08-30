"""A ceiling we imposed is not a strike against a player (invariant 11, ADR-0019, ADR-0021).

`finish_reason: "length"` says an answer was cut off and nothing about who cut it. Two cases, and
they are distinguishable from the response rather than arguable: we know what `max_tokens` we asked
for, and the provider reports what it generated.

* Our ceiling bound it → the harness ended the answer. The turn fails; nobody is forfeited.
* The provider's own ceiling bound it → a strike, and after enough of them a `truncated` forfeit.
  `TRUNCATED` stays rated: with the window arithmetic measured rather than estimated, what is left
  is a model that could not finish a turn inside a budget set far above what any of them need.

The first case is not hypothetical. A miscalculated window asked an endpoint for **one** output
token, every reply came back truncated, and four of them ended a real game `truncated`, `1-0`,
against `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at ply 5.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import sync_model_registry
from chessmark.agents.scripted import scripted, step, tool_call
from chessmark.agents.turn import MAX_TRUNCATIONS, TurnLimits
from chessmark.db.enums import TurnStatus
from chessmark.db.models import ModelEndpoint, ModelRegistry
from chessmark.game import Termination
from tests.agents.conftest import Table, play_turn

pytestmark = pytest.mark.integration


def truncated(*, completion_tokens: int) -> dict[str, object]:
    """A response cut off at `length`, having generated exactly this much."""
    return step(finish_reason="length", completion_tokens=completion_tokens)


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


async def test_our_own_ceiling_fails_the_turn_and_forfeits_nobody(
    db: AsyncSession, table: Table
) -> None:
    """The exact shape of the real forfeit: every response stops at the number we asked for."""
    limits = TurnLimits(max_completion_tokens=4_000)

    result = await play_turn(
        db,
        table,
        scripted(truncated(completion_tokens=4_000), repeat_last=True),
        limits=limits,
    )

    assert result.status is TurnStatus.FAILED
    assert result.outcome is None, "a harness bound is never a finding about a player"
    assert "max_tokens" in (result.error or ""), result.error


async def test_it_does_not_spend_the_strike_budget(db: AsyncSession, table: Table) -> None:
    """It fails on the first one. Counting four of them is how the forfeit was reached."""
    calls = 0

    async def counting(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return truncated(completion_tokens=4_000)

    await play_turn(db, table, counting, limits=TurnLimits(max_completion_tokens=4_000))

    assert calls == 1, f"our own ceiling is not retried into a forfeit, got {calls} calls"


async def test_a_providers_own_ceiling_still_forfeits(db: AsyncSession, table: Table) -> None:
    """Stopping *short* of what we allowed is the endpoint's limit, and that still counts."""
    result = await play_turn(
        db,
        table,
        scripted(truncated(completion_tokens=900), repeat_last=True),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.TRUNCATED


async def test_a_provider_truncation_is_retried_first(db: AsyncSession, table: Table) -> None:
    """A model cut off mid-reasoning is told so and gets to try again — it did not refuse."""
    result = await play_turn(
        db,
        table,
        scripted(
            truncated(completion_tokens=900),
            step(tool_call("make_move", move="e4")),
        ),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.COMPLETED
    assert result.move is not None and result.move.move.san == "e4"


async def test_an_unreported_generation_count_keeps_the_old_behaviour(
    db: AsyncSession, table: Table
) -> None:
    """Unattributable, so nothing changes silently: it is a strike, as it was before."""
    result = await play_turn(
        db,
        table,
        scripted(truncated(completion_tokens=0), repeat_last=True),
        limits=TurnLimits(max_completion_tokens=64_000),
    )

    assert result.status is TurnStatus.FORFEITED
    assert result.outcome is not None
    assert result.outcome.termination is Termination.TRUNCATED
    assert MAX_TRUNCATIONS >= 1

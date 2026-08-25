"""Exact cost, computed from real token counts.

Invariant 4: cost is never estimated. Two consequences shape this module —

* `Decimal` throughout. Per-token prices run to twelve decimal places; float arithmetic would
  introduce error at exactly the scale we are trying to measure.
* When OpenRouter tells us what it charged, that figure wins. Ours is a fallback, not a
  second opinion.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.types import CostSource, TokenUsage
from chessmark.db.models import ModelRegistry


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-token prices in USD."""

    model: str
    prompt_usd_per_token: Decimal = Decimal(0)
    completion_usd_per_token: Decimal = Decimal(0)
    cached_prompt_usd_per_token: Decimal | None = None
    """Discounted rate for cache reads. Falls back to the full prompt rate when unknown."""

    @property
    def is_free(self) -> bool:
        return not self.prompt_usd_per_token and not self.completion_usd_per_token


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    total_usd: Decimal
    source: CostSource
    prompt_usd: Decimal = Decimal(0)
    cached_usd: Decimal = Decimal(0)
    completion_usd: Decimal = Decimal(0)


class PricingTable:
    """Lookup of model slug to pricing."""

    def __init__(self, pricing: dict[str, ModelPricing] | None = None) -> None:
        self._pricing = dict(pricing or {})

    def __contains__(self, model: str) -> bool:
        return self._normalise(model) in self._pricing

    def __len__(self) -> int:
        return len(self._pricing)

    @staticmethod
    def _normalise(model: str) -> str:
        """Strip a LiteLLM routing prefix so `openrouter/x/y` and `x/y` are the same model."""
        return model.removeprefix("openrouter/")

    def get(self, model: str) -> ModelPricing | None:
        return self._pricing.get(self._normalise(model))

    def slugs(self) -> list[str]:
        """Every model in the table. Lets a test assert a property of the seed rather than name a
        model — vendors withdraw them, and a test pinned to one breaks for no useful reason."""
        return sorted(self._pricing)

    def add(self, pricing: ModelPricing) -> None:
        self._pricing[self._normalise(pricing.model)] = pricing

    @classmethod
    async def from_registry(cls, session: AsyncSession) -> PricingTable:
        """Load pricing from `model_registry`.

        The database, not a file. Pricing used to be read from the committed seed snapshot, which
        meant the worker costed calls against whatever the catalogue looked like when someone last
        ran a script — and the registry rows it was costing *against* could say something else
        entirely. One source, refreshed by `make seed-models`.

        Only a fallback either way: when OpenRouter reports what it charged, that figure wins
        (`compute_cost`). This is what answers when it does not.
        """
        rows = await session.scalars(
            sa.select(ModelRegistry).where(ModelRegistry.enabled.is_(True))
        )
        table = cls()
        for row in rows:
            table.add(
                ModelPricing(
                    model=row.openrouter_id,
                    prompt_usd_per_token=row.prompt_usd_per_token,
                    completion_usd_per_token=row.completion_usd_per_token,
                )
            )
        return table


def compute_cost(
    usage: TokenUsage,
    pricing: ModelPricing | None,
    *,
    provider_cost_usd: Decimal | None = None,
) -> CostBreakdown:
    """Cost for one call.

    Order of authority:

    1. What the provider says it charged.
    2. Token counts times registry pricing.
    3. Zero, flagged `UNKNOWN` — so a missing price is visible as missing rather than free.
    """
    if provider_cost_usd is not None:
        return CostBreakdown(total_usd=provider_cost_usd, source=CostSource.PROVIDER)

    if pricing is None:
        return CostBreakdown(total_usd=Decimal(0), source=CostSource.UNKNOWN)

    cached_rate = (
        pricing.cached_prompt_usd_per_token
        if pricing.cached_prompt_usd_per_token is not None
        else pricing.prompt_usd_per_token
    )

    prompt_usd = Decimal(usage.uncached_prompt) * pricing.prompt_usd_per_token
    cached_usd = Decimal(usage.cached) * cached_rate
    completion_usd = Decimal(usage.completion) * pricing.completion_usd_per_token

    return CostBreakdown(
        total_usd=prompt_usd + cached_usd + completion_usd,
        source=CostSource.COMPUTED,
        prompt_usd=prompt_usd,
        cached_usd=cached_usd,
        completion_usd=completion_usd,
    )

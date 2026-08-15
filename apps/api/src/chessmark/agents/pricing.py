"""Exact cost, computed from real token counts.

Invariant 4: cost is never estimated. Two consequences shape this module —

* `Decimal` throughout. Per-token prices run to twelve decimal places; float arithmetic would
  introduce error at exactly the scale we are trying to measure.
* When OpenRouter tells us what it charged, that figure wins. Ours is a fallback, not a
  second opinion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from chessmark.agents.types import CostSource, TokenUsage


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

    def add(self, pricing: ModelPricing) -> None:
        self._pricing[self._normalise(pricing.model)] = pricing

    @classmethod
    def from_seed_file(cls, path: Path) -> PricingTable:
        """Load pricing from `seeds/models.json` — the file `refresh_model_seed.py` writes."""
        entries = json.loads(path.read_text(encoding="utf-8"))
        table = cls()
        for entry in entries:
            table.add(
                ModelPricing(
                    model=entry["openrouter_id"],
                    prompt_usd_per_token=Decimal(str(entry.get("prompt_usd_per_token", 0))),
                    completion_usd_per_token=Decimal(str(entry.get("completion_usd_per_token", 0))),
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

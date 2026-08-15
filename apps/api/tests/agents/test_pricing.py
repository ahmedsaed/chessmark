"""Cost must be exact (invariant 4).

The headline check is a hand-calculated figure: if the arithmetic here drifts, every number on the
leaderboard and every budget cap in ADR-0011 drifts with it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from chessmark.agents.pricing import ModelPricing, PricingTable, compute_cost
from chessmark.agents.types import CostSource, TokenUsage

SEED_FILE = Path(__file__).resolve().parent.parent.parent / "seeds" / "models.json"

# GPT-4o-class pricing, chosen because the arithmetic is easy to verify by hand:
#   $2.50 per million prompt tokens  = 0.0000025 / token
#   $10.00 per million completion    = 0.00001   / token
KNOWN = ModelPricing(
    model="test/known",
    prompt_usd_per_token=Decimal("0.0000025"),
    completion_usd_per_token=Decimal("0.00001"),
)


def test_hand_calculated_cost_matches_to_the_cent() -> None:
    """1,000,000 prompt + 100,000 completion = $2.50 + $1.00 = $3.50."""
    usage = TokenUsage(prompt=1_000_000, completion=100_000)

    breakdown = compute_cost(usage, KNOWN)

    assert breakdown.total_usd == Decimal("3.50")
    assert breakdown.source is CostSource.COMPUTED
    assert breakdown.prompt_usd == Decimal("2.50")
    assert breakdown.completion_usd == Decimal("1.00")


def test_small_costs_keep_full_precision() -> None:
    """A single-token call must not round to zero — turn costs are summed over a whole game."""
    breakdown = compute_cost(TokenUsage(prompt=1, completion=1), KNOWN)

    assert breakdown.total_usd == Decimal("0.0000125")
    assert breakdown.total_usd > 0


def test_cost_is_decimal_not_float() -> None:
    breakdown = compute_cost(TokenUsage(prompt=3, completion=7), KNOWN)
    assert isinstance(breakdown.total_usd, Decimal)


def test_cached_tokens_are_billed_at_the_cached_rate() -> None:
    """Caching is what keeps a 60-move game affordable (ADR-0003), so it must show in the cost."""
    pricing = ModelPricing(
        model="test/cached",
        prompt_usd_per_token=Decimal("0.000003"),
        completion_usd_per_token=Decimal("0.000015"),
        cached_prompt_usd_per_token=Decimal("0.0000003"),  # 10% of the full rate
    )
    usage = TokenUsage(prompt=10_000, completion=100, cached=9_000)

    breakdown = compute_cost(usage, pricing)

    assert breakdown.prompt_usd == Decimal("0.003")  # 1,000 uncached
    assert breakdown.cached_usd == Decimal("0.0027")  # 9,000 cached
    assert breakdown.completion_usd == Decimal("0.0015")
    assert breakdown.total_usd == Decimal("0.0072")


def test_cached_tokens_fall_back_to_the_prompt_rate() -> None:
    """With no cached rate known, never assume a discount we cannot prove."""
    usage = TokenUsage(prompt=1000, completion=0, cached=800)

    breakdown = compute_cost(usage, KNOWN)

    assert breakdown.total_usd == Decimal(1000) * KNOWN.prompt_usd_per_token


def test_provider_reported_cost_wins() -> None:
    """What OpenRouter actually charged beats what we would have calculated."""
    breakdown = compute_cost(
        TokenUsage(prompt=1_000_000, completion=100_000),
        KNOWN,
        provider_cost_usd=Decimal("2.99"),
    )

    assert breakdown.total_usd == Decimal("2.99")
    assert breakdown.source is CostSource.PROVIDER


def test_provider_cost_of_zero_still_wins() -> None:
    """Free models really do cost nothing — that is a fact, not a missing value."""
    breakdown = compute_cost(
        TokenUsage(prompt=500, completion=50), KNOWN, provider_cost_usd=Decimal(0)
    )

    assert breakdown.total_usd == Decimal(0)
    assert breakdown.source is CostSource.PROVIDER


def test_unknown_pricing_is_flagged_not_silently_free() -> None:
    """A missing price must be visible as missing, or a paid model looks free on the dashboard."""
    breakdown = compute_cost(TokenUsage(prompt=1000, completion=100), None)

    assert breakdown.total_usd == Decimal(0)
    assert breakdown.source is CostSource.UNKNOWN


def test_free_model_pricing_is_recognised() -> None:
    free = ModelPricing(model="x/y:free")
    assert free.is_free
    assert compute_cost(TokenUsage(prompt=999, completion=99), free).total_usd == Decimal(0)


# ------------------------------------------------------------------ the table


def test_routing_prefix_is_ignored_on_lookup() -> None:
    """LiteLLM prefixes the slug with `openrouter/`; the registry does not."""
    table = PricingTable({"openai/gpt-oss-20b:free": KNOWN})

    assert table.get("openai/gpt-oss-20b:free") is KNOWN
    assert table.get("openrouter/openai/gpt-oss-20b:free") is KNOWN
    assert "openrouter/openai/gpt-oss-20b:free" in table


def test_unknown_model_returns_none() -> None:
    assert PricingTable().get("nobody/nothing") is None


def test_pricing_loads_from_the_seed_file() -> None:
    """The same file `refresh_model_seed.py` writes is the one the gateway prices against."""
    table = PricingTable.from_seed_file(SEED_FILE)

    assert len(table) >= 10
    pricing = table.get("openai/gpt-oss-20b:free")
    assert pricing is not None
    assert pricing.is_free


def test_every_seeded_model_has_non_negative_pricing() -> None:
    import json

    table = PricingTable.from_seed_file(SEED_FILE)

    for entry in json.loads(SEED_FILE.read_text(encoding="utf-8")):
        pricing = table.get(entry["openrouter_id"])
        assert pricing is not None, f"{entry['openrouter_id']} missing from the table"
        assert pricing.prompt_usd_per_token >= 0
        assert pricing.completion_usd_per_token >= 0


@pytest.mark.parametrize(
    ("prompt", "completion", "expected"),
    [
        (0, 0, Decimal("0")),
        (400, 0, Decimal("0.001")),
        (0, 400, Decimal("0.004")),
    ],
)
def test_cost_scales_linearly(prompt: int, completion: int, expected: Decimal) -> None:
    usage = TokenUsage(prompt=prompt, completion=completion)
    assert compute_cost(usage, KNOWN).total_usd == expected

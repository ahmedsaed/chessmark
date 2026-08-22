"""Provider routing — a benchmark-integrity concern, not a tuning knob.

OpenRouter fans one model id across many providers at different precisions. `deepseek-v4-flash`
has 18 endpoints spanning fp8, fp4, and undeclared. Left unconstrained the router picks whichever
it likes, so two games against "the same model" can be two different contestants, and a leaderboard
row silently averages them.
"""

from __future__ import annotations

import pytest

from chessmark.agents.routing import (
    DEFAULT_QUANTIZATIONS,
    FULL_PRECISION_ONLY,
    ProviderRouting,
    is_first_party,
    unconstrained,
    widen_for_first_party,
)

# ====================================================================== the policy


def test_the_default_excludes_every_four_bit_format() -> None:
    """The whole point. A model served at 4-bit is not the model we mean to be scoring."""
    for quantization in ("int4", "fp4", "mxfp4", "nvfp4"):
        assert quantization not in DEFAULT_QUANTIZATIONS
        assert not ProviderRouting().accepts(quantization)


def test_the_default_excludes_undeclared_precision() -> None:
    """A provider that will not say what it serves could be serving anything."""
    assert not ProviderRouting().accepts("unknown")
    assert not ProviderRouting().accepts(None)


def test_the_default_accepts_eight_bit_and_above() -> None:
    for quantization in ("int8", "fp8", "fp16", "bf16", "fp32"):
        assert ProviderRouting().accepts(quantization)


def test_full_precision_only_rejects_eight_bit() -> None:
    strict = ProviderRouting(quantizations=FULL_PRECISION_ONLY)

    assert strict.accepts("bf16")
    assert not strict.accepts("fp8")


def test_the_request_body_matches_openrouters_shape() -> None:
    body = ProviderRouting(
        quantizations=("fp8", "bf16"), sort="throughput", min_throughput_tps=40
    ).to_request()

    assert body["quantizations"] == ["fp8", "bf16"]
    assert body["sort"] == "throughput"
    assert body["preferred_min_throughput"] == 40
    assert body["allow_fallbacks"] is True


def test_only_and_ignore_are_passed_through() -> None:
    body = ProviderRouting(only=("Google",), ignore=("Sail Research",)).to_request()

    assert body["only"] == ["Google"]
    assert body["ignore"] == ["Sail Research"]


def test_a_policy_round_trips_through_storage() -> None:
    """It is recorded on the game, so a result can always say what it ran under (BENCH-04)."""
    original = ProviderRouting(
        quantizations=("fp8",), sort="latency", min_throughput_tps=25, only=("DeepInfra",)
    )

    restored = ProviderRouting.from_record(original.to_record())

    assert restored.quantizations == ("fp8",)
    assert restored.sort == "latency"
    assert restored.min_throughput_tps == 25
    assert restored.only == ("DeepInfra",)


def test_an_empty_record_falls_back_to_the_safe_default() -> None:
    """A game recorded before routing existed must not read back as unconstrained."""
    assert ProviderRouting.from_record({}).quantizations == DEFAULT_QUANTIZATIONS
    assert ProviderRouting.from_record(None).quantizations == DEFAULT_QUANTIZATIONS


def test_unconstrained_is_available_but_explicit() -> None:
    body = unconstrained().to_request()

    assert "quantizations" not in body
    assert unconstrained().accepts("fp4"), "opting out means opting all the way out"


# ====================================================================== first-party


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("google/gemini-2.5-flash-lite", "Google AI Studio"),
        ("google/gemini-2.5-flash-lite", "Google"),
        ("openai/gpt-5-nano", "OpenAI"),
        ("openai/gpt-5-nano", "Azure"),
        ("deepseek/deepseek-v4-flash", "DeepSeek"),
    ],
)
def test_a_models_own_vendor_is_recognised(model: str, provider: str) -> None:
    assert is_first_party(model, provider)


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("deepseek/deepseek-v4-flash", "Sail Research"),
        ("deepseek/deepseek-v4-flash", "DeepInfra"),
        ("openai/gpt-5-nano", "Google"),
    ],
)
def test_a_third_party_host_is_not(model: str, provider: str) -> None:
    assert not is_first_party(model, provider)


def test_a_closed_weight_model_stays_playable() -> None:
    """Gemini reports `unknown` because there is nothing to disclose — you get whatever Google
    runs. Excluding it on that basis would lock out exactly the models worth benchmarking."""
    endpoints = [
        ("Google", "unknown"),
        ("Google AI Studio", "unknown"),
    ]

    widened = widen_for_first_party(
        ProviderRouting(), model_slug="google/gemini-2.5-flash-lite", endpoints=endpoints
    )

    assert widened.accepts("unknown")
    assert set(widened.only) == {"Google", "Google AI Studio"}, (
        "widening must be pinned to the trusted endpoints, or a third-party unknown slips in behind it"
    )


def test_an_open_weight_model_is_not_widened() -> None:
    """deepseek-v4-flash has an fp8 endpoint, so the strict policy is satisfiable and there is no
    reason to admit `unknown` from DigitalOcean."""
    endpoints = [
        ("StreamLake", "fp8"),
        ("Sail Research", "fp4"),
        ("DigitalOcean", "unknown"),
    ]

    widened = widen_for_first_party(
        ProviderRouting(), model_slug="deepseek/deepseek-v4-flash", endpoints=endpoints
    )

    assert not widened.accepts("unknown")
    assert widened.only == ()


def test_an_untrusted_only_model_is_left_blocked() -> None:
    """If the only thing on offer is an undeclared third-party endpoint, the honest outcome is
    that the model cannot be benchmarked — not that we quietly accept it."""
    endpoints = [("Some Reseller", "unknown"), ("Another", "unknown")]

    widened = widen_for_first_party(
        ProviderRouting(), model_slug="inclusionai/ling-2.6-flash", endpoints=endpoints
    )

    assert not widened.accepts("unknown")


def test_widening_never_admits_four_bit() -> None:
    endpoints = [("OpenAI", "unknown"), ("Sail Research", "fp4")]

    widened = widen_for_first_party(
        ProviderRouting(), model_slug="openai/gpt-5-nano", endpoints=endpoints
    )

    assert not widened.accepts("fp4")
    assert "Sail Research" not in widened.only


# ====================================================================== the pin must survive storage


def test_a_pinned_policy_round_trips_without_regaining_a_filter() -> None:
    """The bug that abandoned a live game at ply 0.

    `to_request()` omits `quantizations` when empty, because OpenRouter reads an absent field as
    "no filter". So a pinned seat stored no key — and `from_record` helpfully substituted the fp8+
    default. `only=["Google AI Studio"]` plus a filter excluding `unknown` matches nothing, and the
    game died with a 404 while its *stored* record looked perfectly correct.
    """
    pinned = ProviderRouting(only=("Google AI Studio",), quantizations=())

    restored = ProviderRouting.from_record(pinned.to_record())

    assert restored.only == ("Google AI Studio",)
    assert restored.quantizations == ()
    assert restored.accepts("unknown"), "the pinned endpoint must not be excluded by a filter"
    assert "quantizations" not in restored.to_request(), (
        "an empty filter must stay absent from the request, or OpenRouter applies it"
    )


def test_a_record_written_before_the_key_was_emitted_is_still_read_as_pinned() -> None:
    """Games created between ADR-0015 and this fix stored `only` and no `quantizations`."""
    legacy_pin = {"only": ["Alibaba"], "sort": "price", "allow_fallbacks": True}

    restored = ProviderRouting.from_record(legacy_pin)

    assert restored.only == ("Alibaba",)
    assert restored.quantizations == ()
    assert restored.accepts("unknown")


def test_a_record_from_before_routing_existed_still_gets_the_safe_default() -> None:
    """The case the old behaviour was protecting, which is why it was written that way."""
    assert ProviderRouting.from_record({}).quantizations == DEFAULT_QUANTIZATIONS
    assert ProviderRouting.from_record(None).quantizations == DEFAULT_QUANTIZATIONS


def test_an_explicit_filter_still_round_trips() -> None:
    explicit = ProviderRouting(quantizations=("fp4",))

    assert ProviderRouting.from_record(explicit.to_record()).quantizations == ("fp4",)

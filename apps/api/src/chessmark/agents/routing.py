"""OpenRouter provider routing.

A single model id on OpenRouter is served by many providers at different precisions —
`deepseek-v4-flash` has 18 endpoints ranging from fp8 down to fp4, and some that declare nothing.
Left unconstrained the router picks whichever it likes, so two games against "the same model" can
be two different contestants.

That is a benchmark-integrity problem, not a performance tuning knob: a leaderboard row that
silently averages fp8 and fp4 runs is measuring the routing lottery as much as the model. The
policy is therefore recorded on every game (BENCH-04), and the provider that actually served each
call is recorded on every `llm_calls` row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Everything OpenRouter can report, worst to best.
ALL_QUANTIZATIONS = (
    "int4",
    "fp4",
    "mxfp4",
    "nvfp4",
    "fp6",
    "int8",
    "fp8",
    "mxfp8",
    "fp16",
    "bf16",
    "fp32",
    "unknown",
)

#: 8-bit and above. Excludes every 4-bit format, `fp6`, and `unknown` — a provider that will not
#: declare its precision could be serving anything.
DEFAULT_QUANTIZATIONS = ("int8", "fp8", "mxfp8", "fp16", "bf16", "fp32")

#: Full precision only. For a ranked run where even 8-bit is more variance than we want to accept.
FULL_PRECISION_ONLY = ("fp16", "bf16", "fp32")


@dataclass(frozen=True, slots=True)
class ProviderRouting:
    """What a game will accept from OpenRouter's router."""

    quantizations: tuple[str, ...] = DEFAULT_QUANTIZATIONS
    sort: str | None = "price"
    min_throughput_tps: float | None = None
    only: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    allow_fallbacks: bool = True
    """Fallbacks stay *within* the quantization filter, so this cannot smuggle in a 4-bit
    endpoint. Turning it off makes a game fail rather than wait when one provider is down."""

    extra: dict[str, Any] = field(default_factory=dict)

    def to_request(self) -> dict[str, Any]:
        """The `provider` field of an OpenRouter request."""
        body: dict[str, Any] = {"allow_fallbacks": self.allow_fallbacks}
        if self.quantizations:
            body["quantizations"] = list(self.quantizations)
        if self.sort:
            body["sort"] = self.sort
        if self.min_throughput_tps:
            body["preferred_min_throughput"] = self.min_throughput_tps
        if self.only:
            body["only"] = list(self.only)
        if self.ignore:
            body["ignore"] = list(self.ignore)
        body.update(self.extra)
        return body

    def to_record(self) -> dict[str, Any]:
        """The form stored on the game, so a result can always say what it ran under.

        Always carries `quantizations`, even empty — unlike `to_request()`, which omits the key
        because OpenRouter reads an absent field as "no filter". A record has to distinguish
        *deliberately cleared* from *never set*, and an omitted key cannot.
        """
        return {**self.to_request(), "quantizations": list(self.quantizations)}

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> ProviderRouting:
        """Read back a stored policy.

        Three cases, and conflating the first two cost a game:

        * `quantizations` present, even empty — honour it exactly. An empty list means the seat is
          pinned to one endpoint and the endpoint *is* the constraint (ADR-0015).
        * absent, but `only` is set — also pinned, by a record written before `to_record` began
          emitting the key. Same treatment.
        * absent entirely — a game from before routing existed. Fall back to the safe default
          rather than reading as unconstrained.

        The bug this fixes: `to_request()` omits an empty `quantizations`, so a pinned seat stored
        no key, and this method helpfully re-added the fp8+ filter. `only=[Google AI Studio]` plus a
        filter that excludes `unknown` matches nothing, and the game was abandoned at ply 0 with a
        404 — with the *stored* record looking perfectly correct.
        """
        record = record or {}
        only = tuple(record.get("only") or ())

        if "quantizations" in record:
            quantizations = tuple(record["quantizations"] or ())
        elif only:
            quantizations = ()
        else:
            quantizations = DEFAULT_QUANTIZATIONS

        return cls(
            quantizations=quantizations,
            sort=record.get("sort"),
            min_throughput_tps=record.get("preferred_min_throughput"),
            only=only,
            ignore=tuple(record.get("ignore") or ()),
            allow_fallbacks=bool(record.get("allow_fallbacks", True)),
        )

    def accepts(self, quantization: str | None) -> bool:
        """Would this policy allow an endpoint at the given precision?

        An empty list means no filter was requested, so everything is accepted — the same thing
        OpenRouter does when the field is omitted. Reading it as "accept nothing" would make
         reject every endpoint, which is the opposite of its name.
        """
        if not self.quantizations:
            return True
        return (quantization or "unknown") in self.quantizations


def unconstrained() -> ProviderRouting:
    """No filtering at all — whatever the router feels like.

    Only appropriate for an explicitly unranked exhibition game, and never for a ranked one.
    """
    return ProviderRouting(quantizations=(), sort=None)


# ---------------------------------------------------------------------- first-party

#: Providers that are the model's own vendor, or an official cloud reselling it unchanged.
#:
#: This distinction is load-bearing. A closed-weight model like `gemini-2.5-flash-lite` or
#: `gpt-5-nano` reports `unknown` because there is nothing to disclose — you get whatever Google or
#: OpenAI runs, and no third party is quantizing anything. An open-weight model like
#: `deepseek-v4-flash` is fanned across eighteen hosts where `unknown` genuinely might be hiding
#: 4-bit. Treating both the same either locks out every frontier model or waves through the
#: quantized ones.
FIRST_PARTY_PROVIDERS: dict[str, frozenset[str]] = {
    "openai": frozenset({"OpenAI", "Azure"}),
    "google": frozenset({"Google", "Google AI Studio", "Google Vertex"}),
    "anthropic": frozenset({"Anthropic", "Amazon Bedrock", "Google Vertex"}),
    "deepseek": frozenset({"DeepSeek"}),
    "x-ai": frozenset({"xAI"}),
    "mistralai": frozenset({"Mistral"}),
    "amazon": frozenset({"Amazon Bedrock"}),
    "cohere": frozenset({"Cohere"}),
    "meta-llama": frozenset({"Meta"}),
    "z-ai": frozenset({"Z.AI"}),
    "qwen": frozenset({"Alibaba"}),
    "moonshotai": frozenset({"Moonshot AI"}),
}


def is_first_party(model_slug: str, provider_name: str) -> bool:
    """Is this endpoint the model's own vendor, or an official cloud reselling it?"""
    vendor = model_slug.split("/", 1)[0].lstrip("~").lower()
    return provider_name in FIRST_PARTY_PROVIDERS.get(vendor, frozenset())


def widen_for_first_party(
    routing: ProviderRouting,
    *,
    model_slug: str,
    endpoints: list[tuple[str, str | None]],
) -> ProviderRouting:
    """Permit `unknown` when every endpoint offering it is the model's own vendor.

    `endpoints` is `(provider_name, quantization)` pairs. If the model already has an endpoint at
    an accepted precision, nothing changes — the strict policy is satisfiable and we keep it. Only
    when the alternative is "this model cannot be played at all" do we admit `unknown`, and then
    only from a first-party endpoint, restricted with `only` so a third-party `unknown` cannot slip
    in behind it.
    """
    if "unknown" in routing.quantizations or not routing.quantizations:
        return routing

    if any(routing.accepts(quant) for _, quant in endpoints):
        return routing

    trusted = sorted(
        {
            name
            for name, quant in endpoints
            if (quant or "unknown") == "unknown" and is_first_party(model_slug, name)
        }
    )
    if not trusted:
        return routing

    return ProviderRouting(
        quantizations=(*routing.quantizations, "unknown"),
        sort=routing.sort,
        min_throughput_tps=routing.min_throughput_tps,
        only=tuple(trusted),
        ignore=routing.ignore,
        allow_fallbacks=routing.allow_fallbacks,
        extra=dict(routing.extra),
    )

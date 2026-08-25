"""Sync the playable-model registry from OpenRouter.

`make seed-models` calls OpenRouter's public model list and upserts it into Postgres. Idempotent,
so it can run on deploy without special handling.

**There is no seed file.** There was one — `seeds/models.json`, written by a script and committed —
and it was a snapshot that silently went stale: it held 239 models against 419 live, and it was
missing the entire frontier tier, because the script that wrote it defaulted to *free* models only.
Nothing surfaced that. A checked-in copy of someone else's catalogue is a cache with no expiry and
no invalidation, and pricing here is load-bearing rather than informational — it backs the budget
caps in ADR-0011, so a stale price is a wrong cap.

The catalogue is fetched at seed time instead, which means seeding needs the network. That is the
trade: a deploy that cannot reach OpenRouter cannot refresh the registry, but the rows already in
Postgres keep serving, so a running system is unaffected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.pricing import ModelPricing, PricingTable
from chessmark.db.models import ModelEndpoint, ModelRegistry

MODELS_URL = "https://openrouter.ai/api/v1/models"


@dataclass(slots=True)
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated)

    def __str__(self) -> str:
        return (
            f"{len(self.created)} created, {len(self.updated)} updated, "
            f"{len(self.disabled)} disabled"
        )


def provider_of(openrouter_id: str) -> str:
    """The vendor half of an OpenRouter slug: `nvidia/nemotron-nano-9b-v2:free` -> `nvidia`."""
    return openrouter_id.split("/", 1)[0] if "/" in openrouter_id else "unknown"


def to_registry_entry(model: dict[str, Any]) -> dict[str, Any]:
    """One OpenRouter catalogue entry, in the shape `sync_model_registry` upserts.

    Prices are read as strings and kept as strings: they run to twelve decimal places and the
    column is `NUMERIC`, so passing them through `float` would round away precision that
    invariant 4 depends on.
    """
    pricing = model.get("pricing") or {}
    supported = model.get("supported_parameters") or []
    model_id: str = model["id"]

    prompt = _price(pricing.get("prompt"))
    completion = _price(pricing.get("completion"))

    return {
        "openrouter_id": model_id,
        "display_name": model.get("name") or model_id,
        "context_length": model.get("context_length"),
        "prompt_usd_per_token": prompt,
        "completion_usd_per_token": completion,
        "credit_cost": credit_cost_for(prompt, completion),
        "supports_reasoning": "reasoning" in supported,
        "supports_tools": "tools" in supported,
        "is_free": model_id.endswith(":free"),
        "enabled": True,
    }


#: Credit price per tier, and the ceiling each tier allows (ADR-0016).
#:
#: Read as: a model costs `credits` if **both** its prices fit under the two ceilings. The last
#: entry is open-ended — anything above the third tier lands there.
#:
#: Ordered cheapest first and evaluated in order, so the first tier a model fits is its tier.
CREDIT_TIERS: tuple[tuple[int, Decimal, Decimal], ...] = (
    (1, Decimal("0.30"), Decimal("1.50")),
    (2, Decimal("2.00"), Decimal("8.00")),
    (3, Decimal("10.00"), Decimal("40.00")),
)

#: What a model above every tier costs. Seventeen models sit here, up to $30/M in and $180/M out —
#: one game against one of them can cost more than everything else on the site put together.
TOP_TIER_CREDITS = 6

PER_MILLION = Decimal(1_000_000)


def credit_cost_for(prompt_usd_per_token: Decimal, completion_usd_per_token: Decimal) -> int:
    """What a seat against this model costs, in credits (ADR-0016).

    **The worse of the two prices decides.** A model that is cheap to prompt and ruinous to
    generate is still a model that can hurt, and the failure is asymmetric: pricing one too low
    costs real money, pricing one too high costs a user a credit.

    A free model is tier 1 rather than free: it still occupies a seat, and the free tier is slow
    and verbose enough that unlimited games against it are their own problem.
    """
    prompt = Decimal(prompt_usd_per_token) * PER_MILLION
    completion = Decimal(completion_usd_per_token) * PER_MILLION

    for credits, max_prompt, max_completion in CREDIT_TIERS:
        if prompt <= max_prompt and completion <= max_completion:
            return credits
    return TOP_TIER_CREDITS


def _price(raw: object) -> Decimal:
    """A catalogue price, or zero.

    OpenRouter reports `-1` for models whose price it cannot state — the `openrouter/auto` router
    entries. Negative is not a discount, and letting one through would make a model look like it
    *earns* money and sort to the top of any cost ordering, so it is treated as unknown.
    """
    if raw is None:
        return Decimal(0)
    try:
        value = Decimal(str(raw))
    except (ArithmeticError, ValueError):
        return Decimal(0)
    return value if value > 0 else Decimal(0)


def is_batch(openrouter_id: str) -> bool:
    """A batch variant, which cannot play a game.

    OpenRouter's `:batch` models are the same weights at half price, served **asynchronously**: a
    job is submitted and collected later, not answered on the chat endpoint a turn calls. A turn
    blocks on a synchronous completion with a 600-second ceiling, so a batch model either errors or
    runs out the clock.

    Running out the clock is the reason this is a registration filter rather than a warning in the
    UI. A turn that times out **forfeits the model** (`TurnLimits.max_seconds`), so an unplayable
    model does not fail politely — it records a loss against a model that never got to move, in the
    one number this project exists to publish.

    Nothing in the data marks them. They declare `tools`, they carry active endpoints, and ours
    report 99% uptime; only the slug says what they are. 60 of the 61 in the catalogue have a
    non-batch sibling, so excluding them costs the field almost nothing.
    """
    return openrouter_id.endswith(":batch")


def fits_a_game(context_length: int | None, minimum: int) -> bool:
    """Whether this model's context window can hold a game worth playing (AGENT-14).

    The transcript is replayed whole on every turn (ADR-0003), so a turn's prompt *is* the context
    it needs, and it grows about **1,818 tokens per ply** — measured across real games, not
    assumed. A 32k window is therefore exhausted around ply 20 of a possible 300.

    That is not a polite failure. `context_exceeded` is in `FORFEIT_TERMINATIONS`, so the model
    records a **loss** it never had a chance to avoid, in the number the leaderboard publishes.

    A model with no declared context length is kept: unknown is not the same as small, and
    excluding on missing metadata would silently drop models over a gap in someone else's data.
    """
    if minimum <= 0 or context_length is None:
        return True
    return context_length >= minimum


async def fetch_catalogue(
    client: httpx.AsyncClient,
    *,
    tools_only: bool = True,
    free_only: bool = False,
    min_context: int = 0,
) -> list[dict[str, Any]]:
    """The live catalogue, ready to upsert.

    Four filters. The first three share a reason — a model that cannot play should not be
    registered, because registering it only invites a confusing failure later, and every one of
    those failures is a forfeit rather than an apology:

    * `tools_only` (default): the runtime acts only through tools (AGENT-01).
    * batch variants: asynchronous, so they cannot answer a turn. See `is_batch`.
    * `min_context`: too small to hold a game. See `fits_a_game`.

    The fourth is different in kind. A floating alias *can* play perfectly well; what it cannot do
    is say what played. Its record is unreproducible, which is a worse failure for a benchmark than
    being unable to move. See `is_floating_alias`.
    """
    response = await client.get(MODELS_URL)
    response.raise_for_status()
    models: list[dict[str, Any]] = response.json()["data"]

    entries = [to_registry_entry(model) for model in models]
    entries = [entry for entry in entries if not is_batch(entry["openrouter_id"])]
    entries = [e for e in entries if not is_floating_alias(e["openrouter_id"])]
    entries = [e for e in entries if fits_a_game(e["context_length"], min_context)]
    if tools_only:
        entries = [entry for entry in entries if entry["supports_tools"]]
    if free_only:
        entries = [entry for entry in entries if entry["is_free"]]
    return entries


async def sync_model_registry(
    session: AsyncSession,
    entries: list[dict[str, Any]],
    *,
    disable_missing: bool = False,
) -> SyncReport:
    """Upsert the registry.

    `disable_missing` marks rows absent from the seed as disabled rather than deleting them —
    a model that has played games must stay resolvable, or its history becomes unreadable.
    """
    report = SyncReport()

    existing_rows = await session.scalars(sa.select(ModelRegistry))
    existing = {row.openrouter_id: row for row in existing_rows}
    seen: set[str] = set()

    for entry in entries:
        slug = entry["openrouter_id"]
        seen.add(slug)

        values = {
            "display_name": entry.get("display_name") or slug,
            "provider": provider_of(slug),
            "context_length": entry.get("context_length"),
            "prompt_usd_per_token": Decimal(str(entry.get("prompt_usd_per_token", 0))),
            "completion_usd_per_token": Decimal(str(entry.get("completion_usd_per_token", 0))),
            "supports_reasoning": bool(entry.get("supports_reasoning", False)),
            "supports_tools": bool(entry.get("supports_tools", True)),
            "is_free": bool(entry.get("is_free", slug.endswith(":free"))),
            "enabled": bool(entry.get("enabled", True)),
            # Derived, and rewritten on every sync so a vendor's price change moves the tier with
            # it. `credit_cost_override` is deliberately absent from this dict: an administrator's
            # exception must survive a refresh (ADR-0016).
            "credit_cost": int(
                entry.get(
                    "credit_cost",
                    credit_cost_for(
                        Decimal(str(entry.get("prompt_usd_per_token", 0))),
                        Decimal(str(entry.get("completion_usd_per_token", 0))),
                    ),
                )
            ),
        }

        row = existing.get(slug)
        if row is None:
            session.add(ModelRegistry(openrouter_id=slug, **values))
            report.created.append(slug)
            continue

        if any(getattr(row, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(row, key, value)
            report.updated.append(slug)

    if disable_missing:
        for slug, row in existing.items():
            if slug not in seen and row.enabled:
                row.enabled = False
                report.disabled.append(slug)

    await session.flush()
    return report


async def load_pricing_table(session: AsyncSession) -> PricingTable:
    """Build the gateway's pricing lookup from the database.

    The registry is the authority at runtime; the seed file only bootstraps it.
    """
    rows = await session.scalars(sa.select(ModelRegistry))
    table = PricingTable()
    for row in rows:
        table.add(
            ModelPricing(
                model=row.openrouter_id,
                prompt_usd_per_token=row.prompt_usd_per_token,
                completion_usd_per_token=row.completion_usd_per_token,
            )
        )
    return table


async def playable_models(session: AsyncSession, *, free_only: bool = False) -> list[ModelRegistry]:
    """Models a game may actually use: enabled, and able to call tools (AGENT-01)."""
    query = sa.select(ModelRegistry).where(
        ModelRegistry.enabled.is_(True), ModelRegistry.supports_tools.is_(True)
    )
    if free_only:
        query = query.where(ModelRegistry.is_free.is_(True))

    rows = await session.scalars(query.order_by(ModelRegistry.openrouter_id))
    return list(rows)


# ---------------------------------------------------------------------- endpoints


ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{slug}/endpoints"

#: Precision, worst first. Used to pick conservatively when a provider appears more than once.
_PRECISION_RANK = {
    "unknown": 0,
    "int4": 1,
    "fp4": 1,
    "mxfp4": 1,
    "nvfp4": 1,
    "fp6": 2,
    "int8": 3,
    "fp8": 3,
    "mxfp8": 3,
    "fp16": 4,
    "bf16": 4,
    "fp32": 5,
}


def _deduplicate(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per provider name, keeping the *lowest* precision on offer.

    OpenRouter lists the same provider more than once for a model — different regions or context
    variants — which collides with one row per provider. Where the duplicates disagree on
    precision, the pessimistic one wins: if a provider might serve us 4-bit, that is the fact worth
    recording, and rounding it up would defeat the point of tracking quantization at all.
    """
    best: dict[str, dict[str, Any]] = {}

    for endpoint in endpoints:
        name = str(endpoint.get("provider_name") or "").strip()
        if not name:
            continue

        incumbent = best.get(name)
        if incumbent is None:
            best[name] = endpoint
            continue

        rank = _PRECISION_RANK.get(str(endpoint.get("quantization") or "unknown"), 0)
        held = _PRECISION_RANK.get(str(incumbent.get("quantization") or "unknown"), 0)
        if rank < held:
            best[name] = endpoint

    return list(best.values())


async def fetch_endpoints(client: Any, openrouter_id: str) -> list[dict[str, Any]]:
    """Every provider serving a model, and at what precision.

    The chat response names the provider but never its quantization, so this is the only way to
    answer "what precision was that game actually played at". The `:free` suffix is not part of
    the endpoints path.
    """
    slug = openrouter_id.split(":", 1)[0]
    response = await client.get(ENDPOINTS_URL.format(slug=slug))
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    endpoints = (payload.get("data") or {}).get("endpoints") or []
    return [e for e in endpoints if isinstance(e, dict)]


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def sync_endpoints(
    session: AsyncSession,
    model: ModelRegistry,
    endpoints: list[dict[str, Any]],
) -> int:
    """Upsert a model's endpoints.

    Rows are never deleted, only deactivated: a game that already ran must stay explicable even
    after a provider disappears.
    """
    existing = {
        row.provider_name: row
        for row in await session.scalars(
            sa.select(ModelEndpoint).where(ModelEndpoint.model_id == model.id)
        )
    }
    seen: set[str] = set()

    for endpoint in _deduplicate(endpoints):
        name = str(endpoint.get("provider_name") or "").strip()
        if not name:
            continue
        seen.add(name)

        values = {
            "quantization": endpoint.get("quantization"),
            "context_length": endpoint.get("context_length"),
            "supports_tools": "tools" in (endpoint.get("supported_parameters") or []),
            "max_completion_tokens": endpoint.get("max_completion_tokens"),
            "is_active": True,
            # Health, as OpenRouter measured it. Selection is by uptime (ADR-0015).
            "uptime_30m": _as_float(endpoint.get("uptime_last_30m")),
            "uptime_1d": _as_float(endpoint.get("uptime_last_1d")),
            "throughput": _as_float(endpoint.get("throughput_last_30m")),
            "latency_ms": _as_float(endpoint.get("latency_last_30m")),
            "supports_implicit_caching": endpoint.get("supports_implicit_caching"),
        }
        row = existing.get(name)
        if row is None:
            session.add(ModelEndpoint(model_id=model.id, provider_name=name, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)

    for name, row in existing.items():
        if name not in seen:
            row.is_active = False

    await session.flush()
    return len(seen)


async def quantization_for(
    session: AsyncSession, *, model_slug: str, provider_name: str
) -> str | None:
    """The precision a named provider serves a model at."""
    return await session.scalar(
        sa.select(ModelEndpoint.quantization)
        .join(ModelRegistry, ModelRegistry.id == ModelEndpoint.model_id)
        .where(
            ModelRegistry.openrouter_id == model_slug,
            ModelEndpoint.provider_name == provider_name,
        )
    )


async def endpoints_for(session: AsyncSession, model_id: uuid.UUID) -> list[ModelEndpoint]:
    rows = await session.scalars(
        sa.select(ModelEndpoint)
        .where(ModelEndpoint.model_id == model_id, ModelEndpoint.is_active.is_(True))
        .order_by(ModelEndpoint.provider_name)
    )
    return list(rows)


# ---------------------------------------------------------------------- pinning


class NoEndpointError(LookupError):
    """No endpoint serves this model at the requested precision.

    Distinct from "we do not know about this model": the caller asked for something specific and it
    does not exist, which is worth saying plainly rather than quietly falling back to whatever else
    is on offer.
    """

    def __init__(self, model_slug: str, quantization: str | None) -> None:
        wanted = quantization or "any precision"
        super().__init__(f"no active endpoint serves {model_slug} at {wanted}")
        self.model_slug = model_slug
        self.quantization = quantization


async def select_endpoint(
    session: AsyncSession,
    *,
    model_slug: str,
    quantization: str | None = None,
) -> ModelEndpoint:
    """The one endpoint a match will use for this seat, for the whole game (ADR-0015).

    **By uptime, highest first, throughput as the tiebreak.** OpenRouter exposes no request counts,
    so "the endpoint most likely to behave" has to be inferred; uptime over the last day measures
    that property more directly than popularity would anyway.

    `quantization` is the contestant's identity, not a filter — asking for `fp4` pins an fp4
    endpoint, and a model with no fp4 endpoint simply has no fp4 contestant.

    Asking for nothing prefers a **declared** precision over `unknown`, then takes the healthiest
    within that. Uptime alone was the first rule and it had a consequence worth avoiding: `unknown`
    wins whenever a reseller happens to be more reliable than the specialists, and it is the one
    value that tells a reader least. `z-ai/glm-4.7` defaulted to Google Vertex at `unknown` (99.98%)
    over Novita at fp8 (95.93%) — recorded honestly, but not what anyone means by "GLM-4.7".
    `unknown` remains a contestant you can ask for; it is no longer the silent default.

    Endpoints that cannot call tools are never selected: an agent that cannot act cannot play
    (AGENT-01), and picking one would produce a forfeit that says nothing about the model.
    """
    query = (
        sa.select(ModelEndpoint)
        .join(ModelRegistry, ModelRegistry.id == ModelEndpoint.model_id)
        .where(
            ModelRegistry.openrouter_id == model_slug,
            ModelEndpoint.is_active.is_(True),
            ModelEndpoint.supports_tools.is_(True),
        )
        .order_by(
            # A declared precision first, unless one was asked for by name. `unknown` is a real
            # contestant but a poor default: it says the least about what actually ran.
            sa.case(
                (ModelEndpoint.quantization.is_(None), 1),
                (ModelEndpoint.quantization == "unknown", 1),
                else_=0,
            ),
            # NULLS LAST: an endpoint whose uptime we have never measured is not a good pick, but
            # it is better than none at all.
            sa.desc(
                sa.func.coalesce(ModelEndpoint.uptime_1d, ModelEndpoint.uptime_30m)
            ).nulls_last(),
            sa.desc(ModelEndpoint.throughput).nulls_last(),
            ModelEndpoint.provider_name,
        )
    )
    if quantization is not None:
        query = query.where(ModelEndpoint.quantization == quantization)

    endpoint = await session.scalar(query.limit(1))
    if endpoint is None:
        raise NoEndpointError(model_slug, quantization)
    return endpoint


async def quantizations_offered(session: AsyncSession, model_slug: str) -> list[str]:
    """Every precision this model can be played at — one contestant per entry (ADR-0015)."""
    rows = await session.scalars(
        sa.select(ModelEndpoint.quantization)
        .join(ModelRegistry, ModelRegistry.id == ModelEndpoint.model_id)
        .where(
            ModelRegistry.openrouter_id == model_slug,
            ModelEndpoint.is_active.is_(True),
            ModelEndpoint.supports_tools.is_(True),
        )
        .distinct()
    )
    return sorted({row or "unknown" for row in rows})


def is_floating_alias(model_slug: str) -> bool:
    """`~vendor/model-latest` points at different weights over time.

    **Never registered** (AGENT-14). ADR-0015 originally kept them playable-but-unrankable, and
    that was half a decision: a rating computed across changing weights is a rating of nothing, but
    so is a *game record* that cannot say which weights played it. BENCH-04 requires a run to record
    its model version, and this slug cannot — so an alias game is unreproducible whether or not
    anyone rates it.

    Kept as a predicate rather than deleted, because rows written before this rule existed still
    need identifying — a game that used one must stay readable.
    """
    return model_slug.startswith("~") or model_slug.endswith("-latest")

"""Sync the playable-model registry from the seed file.

`scripts/refresh_model_seed.py` writes `seeds/models.json` from OpenRouter's public model list;
this loads it into Postgres. Idempotent, so it can run on deploy without special handling.

Pricing here is load-bearing rather than informational: it backs the budget caps in ADR-0011, so
a stale price means a wrong cap.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.pricing import ModelPricing, PricingTable
from chessmark.db.models import ModelEndpoint, ModelRegistry

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "seeds" / "models.json"


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


def load_seed(path: Path | None = None) -> list[dict[str, Any]]:
    seed_path = path or DEFAULT_SEED_PATH
    entries: list[dict[str, Any]] = json.loads(seed_path.read_text(encoding="utf-8"))
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
    """`~vendor/model-latest` points at different weights over time (ADR-0015).

    Playable, never rankable: a rating computed across changing weights is a rating of nothing.
    """
    return model_slug.startswith("~") or model_slug.endswith("-latest")

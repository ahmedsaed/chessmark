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

"""Sync the playable-model registry from the seed file.

`scripts/refresh_model_seed.py` writes `seeds/models.json` from OpenRouter's public model list;
this loads it into Postgres. Idempotent, so it can run on deploy without special handling.

Pricing here is load-bearing rather than informational: it backs the budget caps in ADR-0011, so
a stale price means a wrong cap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.pricing import ModelPricing, PricingTable
from chessmark.db.models import ModelRegistry

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

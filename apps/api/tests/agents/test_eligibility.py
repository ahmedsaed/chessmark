"""AGENT-14, applied to the registry as it already stands.

The rule — no tool calling, no `:batch`, no window under the floor, no floating alias — was
enforced at *admission* and nowhere else. So it held for models arriving after it and not for the
registry as it stood, and two catalogue commands quietly disagreed about whether it applied at all.

`liquid/lfm-2.5-2.6b:free` reached a pool that way: a 65,536-token window against a 128k floor,
asked for a flat 64,000-token completion, refused with *"this endpoint's maximum context length is
65536"*, and the game was abandoned at ply 10 of a real Scotch Game.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chessmark.agents.registry import (
    NoEndpointError,
    context_floor,
    fits_a_game,
    ineligible_reasons,
    playable_models,
    select_endpoint,
    sync_model_registry,
)
from chessmark.db.models import ModelEndpoint, ModelRegistry

FLOOR = 128_000


class TestTheFloorIsTheDefault:
    """The root cause, expressed as a test.

    `min_context: int = 0` meant a caller who forgot the argument silently opted out of the rule —
    and `fits_a_game` reads 0 as *admit everything*. `refresh_catalogue.py` remembered and
    `seed_models.py` did not.
    """

    def test_none_means_the_policy_not_no_filter(self) -> None:
        assert context_floor(None) == FLOOR

    def test_zero_still_disables_it_but_has_to_be_said(self) -> None:
        assert context_floor(0) == 0
        assert fits_a_game(1_024, context_floor(0)), "an explicit 0 is an explicit opt-out"

    def test_an_explicit_floor_wins(self) -> None:
        assert context_floor(200_000) == 200_000

    def test_an_undeclared_window_is_admitted(self) -> None:
        """Unknown is not the same as small. Excluding on missing metadata would drop models over
        a gap in somebody else's data."""
        assert fits_a_game(None, FLOOR)


class TestReasons:
    def _row(self, **kwargs: object) -> ModelRegistry:
        defaults = {
            "openrouter_id": "vendor/model",
            "display_name": "Model",
            "provider": "vendor",
            "context_length": 256_000,
            "supports_tools": True,
        }
        return ModelRegistry(**{**defaults, **kwargs})  # type: ignore[arg-type]

    def test_a_playable_model_has_nothing_against_it(self) -> None:
        assert ineligible_reasons(self._row()) == []

    def test_the_window_that_caused_this(self) -> None:
        against = ineligible_reasons(self._row(context_length=65_536))
        assert against == ["context 65,536 < 128,000"]

    def test_no_tool_calling(self) -> None:
        assert "no tool calling" in ineligible_reasons(self._row(supports_tools=False))

    def test_a_batch_variant(self) -> None:
        reasons = ineligible_reasons(self._row(openrouter_id="vendor/model:batch"))
        assert any("batch" in r for r in reasons)

    def test_a_floating_alias(self) -> None:
        reasons = ineligible_reasons(self._row(openrouter_id="vendor/model-latest"))
        assert any("alias" in r for r in reasons)

    def test_every_reason_is_reported_not_the_first(self) -> None:
        """An operator fixing one and discovering the other has been told half the truth."""
        reasons = ineligible_reasons(
            self._row(openrouter_id="vendor/model-latest", context_length=8_192)
        )
        assert len(reasons) == 2

    def test_having_no_usable_endpoint_counts(self) -> None:
        assert "no active tool-capable endpoint that can hold a game" in ineligible_reasons(
            self._row(), has_endpoint=False
        )


@pytest.mark.integration
class TestTheEndpointIsTheAuthority:
    """A model advertises a context length; an *endpoint* serves one. They need not match.

    The 400 said "**this endpoint's** maximum context length is 65536". Both numbers were already
    in the database and only the model's was ever read — so a model advertising 256k served by a
    64k endpoint passed every gate and failed identically.
    """

    async def _seed(self, db: AsyncSession, *, model_ctx: int, endpoint_ctx: int | None) -> str:
        slug = f"test/ctx-{model_ctx}-{endpoint_ctx}"
        await sync_model_registry(
            db,
            [
                {
                    "openrouter_id": slug,
                    "display_name": slug,
                    "context_length": model_ctx,
                    "supports_tools": True,
                }
            ],
        )
        await db.flush()
        model_id = await db.scalar(
            sa.select(ModelRegistry.id).where(ModelRegistry.openrouter_id == slug)
        )
        db.add(
            ModelEndpoint(
                model_id=model_id,
                provider_name="TestProvider",
                context_length=endpoint_ctx,
                supports_tools=True,
                is_active=True,
                uptime_1d=99.9,
            )
        )
        await db.flush()
        return slug

    async def test_a_small_endpoint_is_never_pinned(self, db: AsyncSession) -> None:
        """Even when the model advertises plenty. This is the case that abandoned a game."""
        slug = await self._seed(db, model_ctx=256_000, endpoint_ctx=65_536)

        with pytest.raises(NoEndpointError):
            await select_endpoint(db, model_slug=slug)

    async def test_a_big_endpoint_is_pinned(self, db: AsyncSession) -> None:
        slug = await self._seed(db, model_ctx=256_000, endpoint_ctx=256_000)

        endpoint = await select_endpoint(db, model_slug=slug)

        assert endpoint.provider_name == "TestProvider"

    async def test_an_endpoint_that_declares_nothing_is_still_pinned(
        self, db: AsyncSession
    ) -> None:
        """Same rule as the model's window: unknown is not small."""
        slug = await self._seed(db, model_ctx=256_000, endpoint_ctx=None)

        assert await select_endpoint(db, model_slug=slug)

    async def test_a_small_model_is_not_playable(self, db: AsyncSession) -> None:
        """`playable_models` checked `enabled` and `supports_tools` and not the window, which is
        how a model too small to finish a game stayed on the list of models a game may use."""
        slug = await self._seed(db, model_ctx=65_536, endpoint_ctx=65_536)

        slugs = [m.openrouter_id for m in await playable_models(db)]

        assert slug not in slugs


@pytest.mark.integration
class TestASyncDoesNotReEnable:
    """The mechanism that made the rule reversible.

    `to_registry_entry` stamps `enabled: True`, and the upsert wrote every value onto the existing
    row — so `refresh-catalogue` disabled a sub-floor model and the next `seed-models` brought it
    straight back. Whether AGENT-14 held depended on which command ran last.
    """

    async def test_a_disabled_row_stays_disabled(self, db: AsyncSession) -> None:
        entry = {
            "openrouter_id": "test/disabled-model",
            "display_name": "Disabled",
            "context_length": 256_000,
            "supports_tools": True,
        }
        await sync_model_registry(db, [entry])
        await db.flush()
        row = await db.scalar(
            sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == entry["openrouter_id"])
        )
        assert row is not None
        row.enabled = False
        await db.flush()

        await sync_model_registry(db, [entry])
        await db.flush()
        await db.refresh(row)

        assert row.enabled is False, "a sync must not undo somebody's decision"

    async def test_a_new_row_is_enabled(self, db: AsyncSession) -> None:
        """Creation still sets it, or nothing a sync discovers would ever be playable."""
        await sync_model_registry(
            db,
            [
                {
                    "openrouter_id": "test/brand-new",
                    "display_name": "New",
                    "context_length": 256_000,
                    "supports_tools": True,
                }
            ],
        )
        await db.flush()
        row = await db.scalar(
            sa.select(ModelRegistry).where(ModelRegistry.openrouter_id == "test/brand-new")
        )
        assert row is not None and row.enabled is True

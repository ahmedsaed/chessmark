"""Endpoint cooldowns — the memory between games (OPS-13).

A real Redis, like the budget tests: the ladder is expressed as key TTLs, and a fake that
approximates `expire` would be testing the fake.
"""

from __future__ import annotations

from typing import Any

import pytest

from chessmark.core.cooldown import LADDER_SECONDS, MAX_SECONDS, ProviderCooldown

pytestmark = pytest.mark.integration

MODEL = "google/gemma-4-26b-a4b-it:free"
PROVIDER = "Google AI Studio"


@pytest.fixture
def cooldown(redis: Any) -> ProviderCooldown:
    return ProviderCooldown(redis)


async def test_the_first_refusal_rests_for_the_first_rung(cooldown: ProviderCooldown) -> None:
    assert await cooldown.note(MODEL, provider=PROVIDER) == LADDER_SECONDS[0]
    assert await cooldown.remaining(MODEL, provider=PROVIDER) > 0


async def test_consecutive_refusals_climb_the_ladder(cooldown: ProviderCooldown) -> None:
    """Escalating matters more than the exact rungs. A pool that waits the same minute every time
    against an endpoint that is down for an hour spends sixty pointless attempts getting there."""
    seen = [await cooldown.note(MODEL, provider=PROVIDER) for _ in range(len(LADDER_SECONDS))]

    assert seen == list(LADDER_SECONDS)
    assert seen == sorted(seen)


async def test_the_ladder_stops_climbing_at_the_top(cooldown: ProviderCooldown) -> None:
    """An endpoint that recovers quietly must be retried within the hour rather than written off
    for the day, so the last rung repeats instead of doubling forever."""
    for _ in range(len(LADDER_SECONDS) + 4):
        rest = await cooldown.note(MODEL, provider=PROVIDER)

    assert rest == MAX_SECONDS


async def test_a_providers_own_hint_wins(cooldown: ProviderCooldown) -> None:
    """It knows and we are guessing. OpenRouter rarely sends one — only when every attempted
    provider returned a retry hint — so this is the case worth honouring when it happens."""
    assert await cooldown.note(MODEL, provider=PROVIDER, retry_after_seconds=1200) == 1200


async def test_a_derisory_hint_is_floored(cooldown: ProviderCooldown) -> None:
    """ "Retry shortly" from a provider that is refusing everything describes its ideal, not our
    experience. Honouring a one-second hint literally is how a retry storm starts."""
    assert await cooldown.note(MODEL, provider=PROVIDER, retry_after_seconds=1) == LADDER_SECONDS[0]


async def test_a_hint_is_still_capped(cooldown: ProviderCooldown) -> None:
    assert await cooldown.note(MODEL, provider=PROVIDER, retry_after_seconds=86_400) == MAX_SECONDS


async def test_a_successful_call_forgets_the_history(cooldown: ProviderCooldown) -> None:
    """Without this the ladder only ever climbs, and an endpoint that was briefly hot last night
    would rest for an hour over its next single refusal."""
    await cooldown.note(MODEL, provider=PROVIDER)
    await cooldown.note(MODEL, provider=PROVIDER)

    await cooldown.clear(MODEL, provider=PROVIDER)

    assert await cooldown.remaining(MODEL, provider=PROVIDER) == 0
    assert await cooldown.note(MODEL, provider=PROVIDER) == LADDER_SECONDS[0]


async def test_one_endpoint_resting_is_not_the_model_resting(cooldown: ProviderCooldown) -> None:
    """A paid model with nineteen endpoints is not unavailable because one of them is. Keying on
    the model alone would have said it was."""
    await cooldown.note("deepseek/deepseek-v4-pro", provider="StreamLake")

    assert await cooldown.remaining("deepseek/deepseek-v4-pro", provider="Baidu") == 0


class TestResting:
    """What the matchmaker asks: of this whole field, who cannot play right now."""

    async def test_finds_a_model_whichever_endpoint_is_resting(
        self, cooldown: ProviderCooldown
    ) -> None:
        """The matchmaker holds slugs; the endpoint was chosen per game (ADR-0015). It must not
        have to know which provider a seat pinned in order to know the model cannot play."""
        await cooldown.note(MODEL, provider=PROVIDER)

        assert await cooldown.resting([MODEL, "other/model:free"]) == {MODEL}

    async def test_says_nothing_when_nothing_is_resting(self, cooldown: ProviderCooldown) -> None:
        assert await cooldown.resting([MODEL, "other/model:free"]) == set()

    async def test_an_empty_field_asks_nothing(self, cooldown: ProviderCooldown) -> None:
        assert await cooldown.resting([]) == set()

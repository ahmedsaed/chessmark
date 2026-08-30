"""402, 401 and 503 pause a game instead of abandoning it (ADR-0017, ADR-0021).

Three codes were unclassified and all three took the same wrong path: not retryable at the gateway,
not "unavailable", not a rejected request — so they fell through to the job retry budget, spent five
attempts, and **abandoned the game**.

* **402** — out of credits. On a free pool this costs nothing and has never bitten; on a paid pool
  it would end every game in flight the moment the balance ran out, rather than waiting for a
  top-up that takes a minute.
* **401** — the key was rejected. Same shape, and abandoning every game over a credentials blip is
  exactly what the comment on `REQUEST_REJECTED_NAMES` says must not happen.
* **503** — *"No available model provider meets your routing requirements"*. Our routing is pinned
  for the whole game (ADR-0015), so this is the provider-404 fact wearing a different code. It was
  in `RETRYABLE_STATUS`, which spent four requests learning that pinned routing had not changed in
  three seconds, before the worker spent five more.

The account cases differ from every other pause in one way that matters: **the endpoint is not
cooled down**, because nothing about it failed.
"""

from __future__ import annotations

from typing import Any

import pytest

from chessmark.agents import llm
from chessmark.agents.types import RateLimit

pytestmark = pytest.mark.integration


class _RefusalError(Exception):
    def __init__(self, status: int, message: str = "refused") -> None:
        super().__init__(message)
        self.status_code = status


# ====================================================================== classification


@pytest.mark.parametrize("status", [401, 402, 503])
def test_the_game_is_paused_rather_than_the_request_rejected(status: int) -> None:
    """All three used to reach `_retry_or_abandon` and end a game after five identical failures."""
    error = _RefusalError(status)

    assert llm.is_unavailable(error), "unavailability pauses; anything else spends the retry budget"
    assert not llm.rejects_the_request(error), "the request was fine"


def test_a_routing_refusal_is_not_retried_at_the_gateway() -> None:
    """Pinned routing does not change in the three seconds a retry ladder waits, so four attempts
    bought four identical answers and cost four requests — the scarce thing (ADR-0017)."""
    assert not llm.is_retryable(_RefusalError(503))
    assert 503 not in llm.RETRYABLE_STATUS


@pytest.mark.parametrize("status", [401, 402])
def test_an_account_refusal_is_marked_as_ours(status: int) -> None:
    limit = llm.rate_limit_from(_RefusalError(status))

    assert limit.account
    assert limit.status_code == status


def test_a_provider_refusal_is_not_marked_as_ours() -> None:
    """The flag decides whether an endpoint gets cooled down, so a false positive would rest a
    model that really did fail."""
    assert not llm.rate_limit_from(_RefusalError(429)).account
    assert not llm.rate_limit_from(_RefusalError(404)).account
    assert not llm.rate_limit_from(_RefusalError(503)).account


# ====================================================================== what a reader is told


def test_an_account_refusal_names_the_account_and_not_the_model() -> None:
    """ "out of credits" attributed to a model would read as a fact about that model, on a page
    whose entire job is publishing facts about models. It is a fact about us."""
    slug = "nvidia/nemotron-3-super-120b-a12b:free"

    broke = RateLimit(status_code=402, account=True).describe(slug)
    unauthorised = RateLimit(status_code=401, account=True).describe(slug)

    assert "credits" in broke
    assert slug not in broke, broke
    assert slug not in unauthorised, unauthorised


def test_a_routing_refusal_names_the_model_because_it_is_about_the_model() -> None:
    """The pinned routing belongs to that seat, so the model is the right subject here."""
    line = RateLimit(status_code=503).describe("z-ai/glm-5.2:free")

    assert "z-ai/glm-5.2:free" in line
    assert "503" in line


# ====================================================================== the endpoint is spared


async def test_an_account_refusal_does_not_cool_the_endpoint_down(
    db: Any, game: Any, make_worker: Any
) -> None:
    """The distinguishing property. Resting an endpoint that never failed would teach the
    matchmaker something false about a model, and it would keep believing it after the top-up."""
    pytest.importorskip("redis")
    noted: list[str] = []

    class _Cooldown:
        async def note(self, model: str, **kwargs: Any) -> int:
            noted.append(model)
            return 60

        async def clear(self, model: str, **kwargs: Any) -> None:
            return None

    async def broke(**_: Any) -> dict[str, Any]:
        raise _RefusalError(402, "insufficient credits")

    worker = make_worker(broke, cooldown=_Cooldown())

    handled = await worker.handle(game.first_job)

    assert str(handled.outcome) == "paused", handled.outcome
    assert noted == [], "the endpoint did not refuse us; our account did"


async def test_a_provider_refusal_still_cools_the_endpoint_down(
    db: Any, game: Any, make_worker: Any
) -> None:
    """The ordinary path must be untouched by the exemption above."""
    noted: list[str] = []

    class _Cooldown:
        async def note(self, model: str, **kwargs: Any) -> int:
            noted.append(model)
            return 60

        async def clear(self, model: str, **kwargs: Any) -> None:
            return None

    async def hot(**_: Any) -> dict[str, Any]:
        raise _RefusalError(429, '{"limit_source":"upstream_provider_shared_pool"}')

    worker = make_worker(hot, cooldown=_Cooldown())

    handled = await worker.handle(game.first_job)

    assert str(handled.outcome) == "paused"
    assert noted, "a provider that rate-limited us is rested between games (OPS-13)"

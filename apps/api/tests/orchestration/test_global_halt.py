"""One switch stops every model call, and lifts itself when it can (OPS-19).

A 402 is OpenRouter's out-of-credits signal — *"Your account or API key has insufficient credits"* —
and [their docs](https://openrouter.ai/docs/api_reference/limits) say it applies to free models too
when the balance is negative. It is a fact about our account, not about a model, an endpoint or a
game, so pausing games one at a time is the wrong shape: thirty pairings each waking every fifteen
minutes to rediscover the same refusal is about 120 doomed requests an hour against an account that
can serve none of them.

The halt is also useful on its own — there was no runtime way to stop spending, because the daily
kill switch is config read at startup.

Two properties do most of the work here. A halt **never ends or forfeits a game**: the turn is not
run, the job is dropped, and the game stays `RUNNING` (invariant 11). And a credit halt **lifts
itself**, because one that only a command lifts leaves the pool idle from an 11pm top-up until
somebody remembers.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from chessmark.agents.scripted import plays
from chessmark.core import credits
from chessmark.core.halt import SOURCE_CREDITS, SOURCE_OPERATOR, Halt
from chessmark.db.enums import GameStatus
from chessmark.db.repositories import get_game
from chessmark.orchestration.reconciler import lift_credit_halt
from chessmark.orchestration.worker import HALTED
from tests.orchestration.conftest import Fixture

pytestmark = pytest.mark.integration


class _RefusalError(Exception):
    def __init__(self, status: int, message: str = "insufficient credits") -> None:
        super().__init__(message)
        self.status_code = status


def _balance(monkeypatch: pytest.MonkeyPatch, remaining: Decimal | None) -> None:
    """What the credits probe will report. `None` stands for "we could not find out"."""

    async def _fetch(_key: str, **_kwargs: Any) -> credits.Balance | None:
        if remaining is None:
            return None
        return credits.Balance(total=remaining, used=Decimal(0))

    monkeypatch.setattr(credits, "fetch_balance", _fetch)
    monkeypatch.setattr("chessmark.orchestration.worker.fetch_balance", _fetch)
    monkeypatch.setattr("chessmark.orchestration.reconciler.fetch_balance", _fetch)


# ====================================================================== the switch


async def test_the_first_halt_wins(redis: Any) -> None:
    """An operator's halt must not be replaced by a self-clearing one, or a credit probe would
    lift a stop a person asked for."""
    halt = Halt(redis)
    await halt.set("I am debugging", source=SOURCE_OPERATOR)

    kept = await halt.set("out of credits", source=SOURCE_CREDITS)

    assert kept.source == SOURCE_OPERATOR
    assert kept.reason == "I am debugging"


async def test_an_unreadable_value_reads_as_running(redis: Any) -> None:
    """The failure mode of this switch should be "we kept going", not "everything stopped and
    nobody could say why"."""
    await redis.set("chessmark:halt", "{not json")

    assert await Halt(redis).state() is None


async def test_clearing_says_whether_anything_was_lifted(redis: Any) -> None:
    halt = Halt(redis)

    assert not await halt.clear()
    await halt.set("stop", source=SOURCE_OPERATOR)
    assert await halt.clear()


# ====================================================================== what a worker does


async def test_a_halted_turn_is_not_run(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("stopped by hand", source=SOURCE_OPERATOR)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == HALTED
    assert handled.result is None, "no provider was reached and nothing was spent"


async def test_a_halt_never_ends_a_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any
) -> None:
    """Invariant 11. A model must never lose a game because we ran out of money."""
    worker = make_worker(plays(["e4"]))
    worker.halt = Halt(redis)
    await worker.halt.set("out of credits", source=SOURCE_CREDITS)

    await worker.handle(game.first_job)

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.RUNNING, "left for the reconciler, not ended"
    assert after.termination is None


async def test_a_402_halts_everything_rather_than_pausing_one_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point. Before this, each of thirty pairings paused and woke separately."""
    _balance(monkeypatch, Decimal(0))

    async def broke(**_: Any) -> dict[str, Any]:
        raise _RefusalError(402)

    worker = make_worker(broke)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert handled.outcome == HALTED
    state = await worker.halt.state()
    assert state is not None
    assert state.source == SOURCE_CREDITS

    db.expunge_all()
    after = await get_game(db, game.game.id)
    assert after.status is GameStatus.RUNNING, "not paused — the halt is what holds it"


async def test_a_402_against_a_funded_account_pauses_only_that_game(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrow case, kept narrow.

    OpenRouter is reported to check a key's remaining budget against `max_tokens` rather than
    actual usage, so a large request can be refused against a balance that would serve a smaller
    one. Halting everything on that would be the `403 → disable` mistake again (ADR-0019): if the
    account visibly has money, this 402 is about the request.
    """
    _balance(monkeypatch, Decimal("25.00"))

    async def broke(**_: Any) -> dict[str, Any]:
        raise _RefusalError(402)

    worker = make_worker(broke)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert str(handled.outcome) == "paused"
    assert await worker.halt.state() is None, "one expensive request must not stop the system"


async def test_a_401_does_not_halt(
    db: Any, game: Fixture, make_worker: Any, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Also account-level, and deliberately narrower: a rejected key is as likely to be one
    misconfigured worker as a dead credential, and halting on it would let a bad deploy of one
    container stop every game the others were playing."""
    _balance(monkeypatch, Decimal(0))

    async def unauthorised(**_: Any) -> dict[str, Any]:
        raise _RefusalError(401, "invalid credentials")

    worker = make_worker(unauthorised)
    worker.halt = Halt(redis)

    handled = await worker.handle(game.first_job)

    assert str(handled.outcome) == "paused"
    assert await worker.halt.state() is None


# ====================================================================== lifting it


async def test_a_credit_halt_lifts_once_the_account_has_credit(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    halt = Halt(redis)
    await halt.set("out of credits", source=SOURCE_CREDITS, balance_usd=Decimal(0))
    _balance(monkeypatch, Decimal("10.00"))

    assert await lift_credit_halt(halt, api_key="k")
    assert await halt.state() is None


async def test_an_operator_halt_is_never_lifted_by_a_probe(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody meant it, and a probe deciding otherwise would be the system overruling them."""
    halt = Halt(redis)
    await halt.set("I am debugging", source=SOURCE_OPERATOR)
    _balance(monkeypatch, Decimal("500.00"))

    assert not await lift_credit_halt(halt, api_key="k")
    assert await halt.state() is not None


@pytest.mark.parametrize("remaining", [None, Decimal(0)])
async def test_uncertainty_leaves_the_halt_standing(
    redis: Any, monkeypatch: pytest.MonkeyPatch, remaining: Decimal | None
) -> None:
    """A probe that could not answer has not learned there is money, which is not the same as
    learning there is — and only the second lifts a stop."""
    halt = Halt(redis)
    await halt.set("out of credits", source=SOURCE_CREDITS)
    _balance(monkeypatch, remaining)

    assert not await lift_credit_halt(halt, api_key="k")
    assert await halt.state() is not None


async def test_the_probe_is_rate_limited_across_reconcilers(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The halt exists because the account cannot serve requests, so the probe must not become the
    thing it was built to prevent."""
    calls = 0

    async def counting(_key: str, **_kwargs: Any) -> credits.Balance | None:
        nonlocal calls
        calls += 1
        return credits.Balance(total=Decimal(0), used=Decimal(0))

    monkeypatch.setattr("chessmark.orchestration.reconciler.fetch_balance", counting)

    halt = Halt(redis)
    await halt.set("out of credits", source=SOURCE_CREDITS)

    for _ in range(5):
        await lift_credit_halt(halt, api_key="k", redis=redis)

    assert calls == 1, f"one probe per interval, shared by every reconciler; made {calls}"


# ====================================================================== the balance itself


def test_a_missing_field_is_ignorance_rather_than_zero() -> None:
    """Zero is a balance and `None` is ignorance. Confusing them would either lift a halt that
    should stand or keep one that should not."""
    assert credits.parse({"data": {"total_credits": 5, "total_usage": 1}}) is not None
    assert credits.parse({"data": {"total_credits": 5}}) is None
    assert credits.parse({"data": "nope"}) is None
    assert credits.parse({}) is None


def test_a_spent_balance_is_not_positive() -> None:
    assert not credits.Balance(total=Decimal(10), used=Decimal(10)).positive
    assert credits.Balance(total=Decimal(10), used=Decimal("9.99")).positive


def test_the_state_survives_a_round_trip() -> None:
    """`balance_usd` is what tells an empty account from an over-large request, so it has to come
    back the way it went in."""
    from chessmark.core.halt import HaltState

    state = HaltState(
        reason="r", source=SOURCE_CREDITS, at=dt.datetime.now(dt.UTC), balance_usd=Decimal("1.50")
    )

    assert state.self_clearing
    assert state.balance_usd == Decimal("1.50")

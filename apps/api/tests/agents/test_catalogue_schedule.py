"""The catalogue refreshes itself on a schedule (OPS-23).

`refresh_catalogue.py` opens with *"built to be scheduled rather than remembered"* and then
**nothing scheduled it**. There was no cron container, no systemd timer, no workflow, and no call
from the API's lifespan, the worker's reconcile loop or the tournament runner: it ran when somebody
typed `make refresh-catalogue`. Prices set the spend caps *and* what users are charged in credits
(ADR-0016), and endpoint rows are what the picker pins to — so the cost of nobody remembering is a
wrong cap, a wrong price and a field of models that cannot be played.

The schedule is `--every HOURS`, which is what the stack's `catalogue` service runs. The
assertions here are about the loop's failure behaviour, because that is the part a container makes
dangerous: a scheduled sweep that exits on a bad night is restarted by Docker straight back into
the same bad night, which is a tight retry loop against a provider that is already unhappy.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
_cli = importlib.import_module("refresh_catalogue")


class _StopError(Exception):
    """Raised from the patched sleep to end an otherwise endless loop."""


def _passes(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> list[float]:
    """Drive `forever` through `outcomes` — an int is a return code, an exception is raised.

    Returns the sleeps it asked for, which is how many passes it survived.
    """
    remaining = list(outcomes)
    slept: list[float] = []

    async def _refresh(**_: Any) -> int:
        result = remaining.pop(0)
        if isinstance(result, Exception):
            raise result
        return int(result)

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)
        if not remaining:
            raise _StopError

    monkeypatch.setattr(_cli, "refresh", _refresh)
    monkeypatch.setattr(_cli.asyncio, "sleep", _sleep)
    return slept


async def test_the_first_pass_runs_before_the_first_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack that has just come up must not serve stale prices until the timer happens to fire.
    The sweep costs nothing — OpenRouter's `/models` and `/endpoints` are metadata, not inference —
    so there is no reason to make a deploy wait for it."""
    slept = _passes(monkeypatch, [0])

    with pytest.raises(_StopError):
        await _cli.forever(every_hours=12)

    assert slept == [12 * 3600], "one pass, then the interval"


async def test_a_failed_pass_does_not_stop_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one that matters in a container. Exiting hands the failure to Docker's restart policy,
    which puts the process back into the same failure — and this stack has already watched its
    tournament runner exit-loop for exactly that reason."""
    slept = _passes(monkeypatch, [RuntimeError("openrouter is down"), 0])

    with pytest.raises(_StopError):
        await _cli.forever(every_hours=6)

    assert len(slept) == 2, "it waited and tried again rather than dying"


async def test_a_refusal_to_touch_the_registry_also_keeps_the_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty catalogue makes `refresh` return 1 with the registry untouched, which is a *good*
    outcome — it is the guard against wiping the registry on a bad response — and must not read as
    a reason to give up on the schedule."""
    slept = _passes(monkeypatch, [1, 0])

    with pytest.raises(_StopError):
        await _cli.forever(every_hours=1)

    assert len(slept) == 2


async def test_the_interval_is_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seconds would be an easy and expensive mistake: `--every 12` meaning twelve seconds is 7,200
    sweeps a day against somebody else's API."""
    slept = _passes(monkeypatch, [0])

    with pytest.raises(_StopError):
        await _cli.forever(every_hours=0.5)

    assert slept == [1800]


@pytest.mark.parametrize("value", ["0", "-1"])
async def test_a_non_positive_interval_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Refused rather than clamped. A zero interval is a busy loop against OpenRouter, and whoever
    typed it meant something — it was not this."""
    monkeypatch.setattr(sys, "argv", ["refresh_catalogue.py", "--every", value])

    assert await _cli.main() == 2


async def test_no_interval_runs_once_and_reports_what_happened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-shot path is what a person and a deploy step run, and it must still fail loudly:
    the exit code is the only thing either of them reads."""
    calls = 0

    async def _refresh(**_: Any) -> int:
        nonlocal calls
        calls += 1
        return 1

    monkeypatch.setattr(_cli, "refresh", _refresh)
    monkeypatch.setattr(sys, "argv", ["refresh_catalogue.py"])

    assert await _cli.main() == 1
    assert calls == 1, "once, not forever"

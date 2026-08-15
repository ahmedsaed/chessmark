"""Recorded provider responses, replayed.

The suite never calls a provider. Fixtures live in `tests/fixtures/llm/` and are refreshed
deliberately with `make record-llm`, never as a side effect of running tests — a missing cassette
raises rather than quietly falling back to a live call.

Each fixture declares a `source`:

* ``live``      — recorded from a real free OpenRouter model.
* ``synthetic`` — hand-authored for a shape we cannot reach without paying (Anthropic caching), or
  for a failure mode that is hard to provoke on demand (malformed tool arguments).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "llm"


@dataclass(frozen=True, slots=True)
class Cassette:
    name: str
    model: str
    source: str
    note: str
    request: dict[str, Any]
    response: dict[str, Any]

    @property
    def is_live_recording(self) -> bool:
        return self.source == "live"


def cassette_names() -> list[str]:
    return sorted(path.stem for path in FIXTURE_DIR.glob("*.json"))


def load_cassette(name: str) -> Cassette:
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(cassette_names()) or "none"
        msg = (
            f"no cassette named {name!r} in {FIXTURE_DIR}. "
            f"Available: {available}. Record one with `make record-llm` — "
            "tests must never fall back to a live provider call."
        )
        raise FileNotFoundError(msg)

    payload = json.loads(path.read_text(encoding="utf-8"))
    return Cassette(
        name=payload["name"],
        model=payload["model"],
        source=payload["source"],
        note=payload.get("note", ""),
        request=payload.get("request", {}),
        response=payload["response"],
    )


def load_all_cassettes() -> list[Cassette]:
    return [load_cassette(name) for name in cassette_names()]


def replay(name: str) -> Callable[..., Any]:
    """A `completion_fn` for `LlmGateway` that returns a recorded response."""
    cassette = load_cassette(name)

    async def _replay(**_kwargs: Any) -> dict[str, Any]:
        return cassette.response

    return _replay


def responds_with(response: dict[str, Any]) -> Callable[..., Any]:
    """A `completion_fn` returning a literal payload, for one-off shapes."""

    async def _respond(**_kwargs: Any) -> dict[str, Any]:
        return response

    return _respond


def fails_with(*errors: BaseException) -> Callable[..., Any]:
    """A `completion_fn` that raises each error in turn, then succeeds.

    Used to exercise the retry classifier without waiting on a real flaky provider.
    """
    queue = list(errors)
    calls = {"count": 0}

    async def _fail(**_kwargs: Any) -> dict[str, Any]:
        calls["count"] += 1
        if queue:
            raise queue.pop(0)
        return {
            "model": "synthetic/recovered",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"content": "recovered"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    _fail.calls = calls  # type: ignore[attr-defined]
    return _fail

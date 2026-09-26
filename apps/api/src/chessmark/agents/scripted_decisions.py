"""A scripted stand-in for a decision model (ADR-0049).

`scripted.py`'s counterpart for the Decisions API. It plugs in as `DecisionGateway(decide_fn=...)`,
so a test — or a `--scripted` worker with no key — runs the real path: request building, parsing,
validation, costing, the verbatim record and the worker's handling of the result. Only the network
is replaced.

    gateway = DecisionGateway(decide_fn=deciding(moves=["e4", "Nf3"], answers={"resign": 0.9}))

It answers from the request it is sent, the way the real endpoint does: the options are whatever
the request offered, so it can never answer a question it was not asked — and a test that wants it
to misbehave says so explicitly (`malformed`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

DecideFn = Callable[..., Any]

#: The build a scripted answer claims to be, so a record says plainly that nothing real answered.
SCRIPTED_BUILD_SUFFIX = "-scripted"


def _weighted(options: list[str], weights: dict[str, float]) -> dict[str, Any]:
    """A choice whose probabilities are the given weights, with the rest of the mass on the first
    option — `play_on`, for the action question — and the highest-weighted option chosen."""
    named = {option: float(weights[option]) for option in options if option in weights}
    rest = max(0.0, 1.0 - sum(named.values()))
    probabilities = {option: named.get(option, 0.0) for option in options}
    probabilities[options[0]] = named.get(options[0], rest)
    chosen = max(options, key=lambda option: (probabilities[option], -options.index(option)))
    return {
        "type": "choice",
        "choice": chosen,
        "probabilities": probabilities,
        "confidence": 0.5,
    }


def _choice(options: list[str], chosen: str) -> dict[str, Any]:
    # Most of the mass on the chosen option and the rest spread evenly, so a distribution drawn from
    # it looks like one: a single spike at 1.0 would hide a page that only ever showed the winner.
    rest = [option for option in options if option != chosen]
    spread = 0.4 / len(rest) if rest else 0.0
    probabilities = {option: round(spread, 4) for option in rest}
    probabilities[chosen] = round(1.0 - spread * len(rest), 4)
    return {
        "type": "choice",
        "choice": chosen,
        "probabilities": probabilities,
        "confidence": 0.5,
    }


def deciding(
    *,
    moves: Iterable[str] = (),
    answers: dict[str, float] | None = None,
    by_side: dict[str, dict[str, float]] | None = None,
    cost: float = 0.000_05,
    input_tokens: int = 1_200,
    provider: str = "Scripted",
    malformed: bool = False,
) -> DecideFn:
    """A decision model that plays `moves` in order where it can, and the first option where not.

    `moves` is consumed one per `move` question, whatever the colour, so two seats sharing one of
    these alternate through the list exactly as a game alternates. A move not on offer — the script
    has drifted from the game — falls back to the first option, which keeps a scripted game playing
    rather than stopping it on a typo. `answers` weights the options of every other `choice` — the
    action question — by option key (`{"resign": 0.9}`); what is left goes to the first option,
    `play_on`, and the heaviest option is chosen. Unnamed options weigh nothing, so a scripted seat
    never resigns, claims, offers or accepts unless a test asks it to. `by_side` overrides them for
    one colour (`"white"`), read from the request's own `you_are`, for a test that needs the two
    seats to answer differently.

    `malformed` answers the move question with an option that was never offered, which is the one
    thing the gateway must refuse before a referee sees it.
    """
    script = list(moves)
    nouls = answers or {}
    calls: list[dict[str, Any]] = []

    async def _decide(request: dict[str, Any]) -> dict[str, Any]:
        calls.append(request)
        out: dict[str, Any] = {}
        side = (by_side or {}).get(str((request.get("state") or {}).get("you_are")), {})
        for key, question in (request.get("questions") or {}).items():
            if question.get("type") != "choice":
                continue
            options = list(question.get("criteria") or {})
            if key == "move":
                wanted = script.pop(0) if script else None
                chosen = wanted if wanted is not None and wanted in options else options[0]
                out[key] = _choice(options, chosen)
                if malformed:
                    out[key]["choice"] = "not-an-option"
            else:
                out[key] = _weighted(options, {**nouls, **side})
        return {
            "id": f"gen-dec-scripted-{len(calls)}",
            "model": f"{request.get('model')}{SCRIPTED_BUILD_SUFFIX}",
            "provider": provider,
            "answers": out,
            "usage": {"input_tokens": input_tokens, "output_tokens": 40, "cost": cost},
        }

    _decide.calls = calls  # type: ignore[attr-defined]
    return _decide


__all__ = ["SCRIPTED_BUILD_SUFFIX", "deciding"]

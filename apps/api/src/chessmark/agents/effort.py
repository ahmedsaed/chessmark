"""Reasoning effort: part of what a contestant is (ADR-0067).

The harness never asked for an effort, so every model reasoned at its provider's default — which
was neither recorded nor fixed. In game `5ec9cee5` Claude Opus 5.5 played at **high** and GPT-6.1
Sol at **medium**, and the game measured those two settings as much as the two models. A provider
can change a default without notice, and a rating would move with no version marking it.

So an effort is a **level**, settled when a seat is created, sent on every call and recorded on the
seat. The levels are OpenRouter's efforts plus two of ours:

* `none` — reasoning off. A real contestant for a model whose reasoning is optional, and the only
  level of one that cannot reason at all (for which nothing is sent: there is nothing to switch).
* `auto` — reasoning on, with the effort left to the model. The only "on" there is for a model that
  reasons but lists no efforts, and what an always-on model does when it names no default.

Pure: no database, no network. `agents/registry.py` stores what `levels_from_catalogue` derives,
and the gateway sends what `request_body` builds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

NONE = "none"
AUTO = "auto"

#: Every effort OpenRouter names, least to most. Ordered so a level list reads the same everywhere.
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")

#: Every level a seat can carry, in the order the form offers them.
LEVELS = (NONE, AUTO, *EFFORTS)

#: OpenRouter's own ratios for turning an effort into a thinking budget on a budget-based model:
#: `budget_tokens = max_tokens * ratio`. Stated here because we send the budget ourselves for those
#: models — see `budget_based`.
BUDGET_RATIO = {
    "minimal": 0.1,
    "low": 0.2,
    "medium": 0.5,
    "high": 0.8,
    "xhigh": 0.95,
    "max": 0.95,
}

#: Anthropic's floor for a thinking budget. Below it the provider refuses the request.
MIN_BUDGET = 1024


@dataclass(frozen=True, slots=True)
class Levels:
    """What a model can be asked for, and what it plays at when nobody chooses."""

    offered: tuple[str, ...]
    default: str

    def __post_init__(self) -> None:
        if self.default not in self.offered:
            raise ValueError(f"default {self.default!r} is not among {self.offered!r}")


def _ordered(levels: set[str]) -> tuple[str, ...]:
    return tuple(level for level in LEVELS if level in levels)


def levels_from_catalogue(*, supports_reasoning: bool, reasoning: dict[str, Any] | None) -> Levels:
    """The levels a model offers, from its OpenRouter catalogue entry.

    `reasoning` is the entry's `reasoning` block: `mandatory`, `default_enabled`,
    `supported_efforts`, `default_effort`. 336 of 469 models carried one when this was written, in
    a handful of shapes, and **`default_effort` does not mean reasoning is on**: 19 models are off
    by default and still name one, because it is the effort used when reasoning is switched on
    without naming an effort.

    So "on by default" is read from `mandatory` and `default_enabled` alone, and an unstated
    `default_enabled` is read as off. That is the conservative reading. Anthropic's optional thinking
    is off unless requested, which is the largest group it covers, and the old seats this may
    mislabel are labelled from the reasoning tokens they actually produced, not from this.
    """
    block = reasoning or {}
    if not supports_reasoning and not block:
        return Levels(offered=(NONE,), default=NONE)

    mandatory = bool(block.get("mandatory"))
    on_by_default = mandatory or block.get("default_enabled") is True
    efforts = {e for e in (block.get("supported_efforts") or []) if e in LEVELS}
    named_default = block.get("default_effort")

    offered = set(efforts)
    if not efforts:
        offered.add(AUTO)
    if mandatory:
        # A model that always reasons cannot be asked not to. `none` in its list would be a level
        # the provider refuses or silently ignores.
        offered.discard(NONE)
    else:
        offered.add(NONE)

    if not on_by_default:
        default = NONE
    elif named_default in offered:
        default = str(named_default)
    else:
        # On by default with no usable default effort named: the model's own choice.
        offered.add(AUTO)
        default = AUTO

    return Levels(offered=_ordered(offered), default=default)


def budget_based(model_slug: str) -> bool:
    """Whether OpenRouter turns this model's effort into a thinking budget taken from `max_tokens`.

    Claude before 5.x. OpenRouter computes `budget_tokens = max_tokens * ratio` for those, and our
    `max_tokens` is not constant: it is held back on a game's first, unmeasured call and shrinks as
    the window fills (`compaction.completion_cap`). The same effort would buy a different budget
    on different turns, so for these we send the budget ourselves (`request_body`).

    Claude 5.x thinks adaptively and maps `effort` straight to `output_config.effort`; it ignores a
    token budget entirely, so sending one there would quietly *lose* the effort.
    """
    vendor, _, name = model_slug.lstrip("~").partition("/")
    if vendor != "anthropic":
        return False
    match = re.search(r"claude-(?:[a-z]+-)?(\d+)", name)
    return match is not None and int(match.group(1)) < 5


def request_body(
    level: str | None,
    *,
    model_slug: str,
    can_reason: bool,
    max_tokens: int | None = None,
    nominal_max_tokens: int | None = None,
) -> dict[str, Any] | None:
    """The `reasoning` field to send, or `None` to send nothing.

    `None` for no level (a seat from before ADR-0067, or one whose model was never synced), and for
    `none` on a model that cannot reason: there is nothing to switch off, and sending the field would
    change a request that is byte-stable today for no reason.

    `nominal_max_tokens` is the seat's stable completion ceiling. On a budget-based model the
    budget is taken from it rather than from this call's `max_tokens`, so it does not drift, and it
    is clamped under `max_tokens` only when the call cannot hold it.
    """
    if level is None:
        return None
    if level == NONE:
        return {"enabled": False} if can_reason else None
    if level == AUTO:
        return {"enabled": True}

    if budget_based(model_slug) and nominal_max_tokens:
        budget = max(int(nominal_max_tokens * BUDGET_RATIO[level]), MIN_BUDGET)
        if max_tokens is not None:
            # The budget must leave room to answer: OpenRouter requires `max_tokens` strictly
            # above it. Near the end of a window that clamp is the drift we could not avoid.
            budget = min(budget, max_tokens - 1)
        if budget >= MIN_BUDGET:
            return {"enabled": True, "max_tokens": budget}
        # No room for even the floor: send the effort and let OpenRouter size what is left.

    return {"enabled": True, "effort": level}


def call_timeout(level: str | None, base: float) -> float:
    """How long one call may take at this level.

    The heaviest efforts think for longer than the base timeout allows on a hard position, and a
    call that times out fails the turn (ADR-0019) — a harness bound, not a finding, but an
    abandoned game all the same. Doubled for `xhigh` and `max`, which is where it bites.
    """
    return base * 2 if level in ("xhigh", "max") else base

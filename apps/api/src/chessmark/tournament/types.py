"""The vocabulary a tournament is described in.

Deliberately free of *which* models are playing. A tournament here is a format, a field, and a set
of bounds — "the free models", "open weights against closed", "everything from one vendor" are all
the same machinery with a different `FieldFilter`. Hard-coding a field would mean a new code path
per idea, and the ideas are the fun part.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class Format(StrEnum):
    """How the field is paired.

    `ROUND_ROBIN` is exact and quadratic: everyone plays everyone, which settles the order beyond
    argument and costs `n(n-1)/2` games — 56 for eight models played twice, 65,280 for the whole
    catalogue. `SWISS` pairs players on similar scores for a fixed number of rounds, which is what
    makes a large field affordable: 12 models over 5 rounds is 30 games instead of 132, and the
    leaders still meet each other, which is what decides a winner.
    """

    ROUND_ROBIN = "round_robin"
    SWISS = "swiss"


class Colour(StrEnum):
    WHITE = "white"
    BLACK = "black"


@dataclass(frozen=True, slots=True)
class Entrant:
    """One seat in a tournament.

    `key` is whatever the caller uses to identify a contestant — a model slug, or a
    `(model, quantization)` pair rendered as a string (ADR-0015). This module never interprets it.
    """

    key: str
    seed: int = 0
    label: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("an entrant needs a key")


@dataclass(frozen=True, slots=True)
class Pairing:
    """One game to play. `black` is None for a bye."""

    white: str
    black: str | None
    round_number: int

    @property
    def is_bye(self) -> bool:
        return self.black is None

    @property
    def pair(self) -> frozenset[str]:
        """Order-independent identity, for asking whether two entrants have already met."""
        return frozenset({self.white} if self.black is None else {self.white, self.black})


@dataclass(frozen=True, slots=True)
class Result:
    """A finished game, from the tournament's point of view.

    `score` is White's: 1 for a win, 0 for a loss, 0.5 for a draw. A bye is recorded as a win for
    `white` with `black` None.
    """

    white: str
    black: str | None
    white_score: float
    round_number: int

    def __post_init__(self) -> None:
        if self.white_score not in (0.0, 0.5, 1.0):
            raise ValueError(f"a chess result is 0, 0.5 or 1, not {self.white_score}")

    def score_for(self, key: str) -> float:
        if key == self.white:
            return self.white_score
        if key == self.black:
            return 1.0 - self.white_score
        raise KeyError(f"{key} did not play this game")

    @property
    def players(self) -> tuple[str, ...]:
        return (self.white,) if self.black is None else (self.white, self.black)


@dataclass(frozen=True, slots=True)
class FieldFilter:
    """Which models enter. Every criterion is optional and they compose with AND.

    This is the whole reason the tournament runner is not written once per idea:

    - the free-model tournament is `FieldFilter(free_only=True)`
    - open weights against closed is two tournaments, or one with `open_weights` set either way
    - a vendor or country bracket is `providers=(...)`, resolved by the caller
    - a rematch of a specific set is `slugs=(...)`

    Resolving a filter against the registry is a database concern and lives in `db/`; this is only
    the description of what was asked for, so it can be stored, replayed and reasoned about.
    """

    slugs: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    free_only: bool | None = None
    open_weights: bool | None = None
    min_credit_cost: int | None = None
    max_credit_cost: int | None = None
    min_context_tokens: int | None = None
    requires_reasoning: bool | None = None
    #: Cap on how many models enter, applied after ordering. `None` means everything that matches.
    limit: int | None = None

    def describe(self) -> str:
        """A one-line summary, for a standings page that should say what it selected."""
        parts: list[str] = []
        if self.slugs:
            parts.append(f"{len(self.slugs)} named models")
        if self.providers:
            parts.append("from " + ", ".join(self.providers))
        if self.free_only is True:
            parts.append("free only")
        elif self.free_only is False:
            parts.append("paid only")
        if self.open_weights is True:
            parts.append("open weights")
        elif self.open_weights is False:
            parts.append("closed weights")
        if self.min_credit_cost is not None:
            parts.append(f"≥{self.min_credit_cost} credits")
        if self.max_credit_cost is not None:
            parts.append(f"≤{self.max_credit_cost} credits")
        if self.requires_reasoning:
            parts.append("reasoning models")
        if self.limit is not None:
            parts.append(f"first {self.limit}")
        return ", ".join(parts) if parts else "every playable model"


@dataclass(frozen=True, slots=True)
class TournamentConfig:
    """The bounds a tournament runs under.

    `max_usd` is the tournament's own ceiling and is deliberately independent of any user's quota
    (ADR-0011 layers exist for a person spending; this is the harness spending on its own
    initiative). `max_concurrent` matters more than it looks: free models are served from a shared
    pool that rate-limits, and the free daily request allowance is consumed at roughly the rate one
    game generates it, so a free tournament wants exactly one game in flight.
    """

    format: Format = Format.ROUND_ROBIN
    #: Round robin only. Each pair meets twice, with colours reversed the second time.
    double: bool = False
    #: Swiss only. Ignored for round robin, whose length is decided by the field.
    rounds: int = 5
    max_concurrent: int = 1
    max_usd: Decimal | None = None
    max_plies_per_game: int = 300
    max_usd_per_game: Decimal | None = None
    is_ranked: bool = True
    field: FieldFilter = field(default_factory=FieldFilter)

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if self.format is Format.SWISS and self.rounds < 1:
            raise ValueError("a Swiss tournament needs at least one round")
        if self.max_usd is not None and self.max_usd <= 0:
            raise ValueError("max_usd must be positive, or None for no ceiling")

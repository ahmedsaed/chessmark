"""Tournaments: a format, a field, and a set of bounds.

Pure, like `game/` and `bench/`. Pairing and standings are decided from the results so far and
nothing else — no scheduler state, no database — which is what makes a tournament resumable: the
next round is *derived* from what has been played, never from where a crashed process had got to.

Nothing here knows what a free model is. "The free models", "open weights against closed", "one
vendor's catalogue" are the same machinery with a different `FieldFilter`.
"""

from chessmark.tournament.pairing import ordered, round_robin, schedule, swiss_round
from chessmark.tournament.standings import Standing, standings
from chessmark.tournament.types import (
    Colour,
    Entrant,
    FieldFilter,
    Format,
    Pairing,
    Result,
    TournamentConfig,
)

__all__ = [
    "Colour",
    "Entrant",
    "FieldFilter",
    "Format",
    "Pairing",
    "Result",
    "Standing",
    "TournamentConfig",
    "ordered",
    "round_robin",
    "schedule",
    "standings",
    "swiss_round",
]

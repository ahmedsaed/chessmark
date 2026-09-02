"""Rating and aggregate metrics.

Pure like `game/`: this package computes, it does not fetch. Nothing here imports `db`, `agents`,
or `api`, so the rating maths can be tested against a published worked example rather than against
a database.
"""

from chessmark.bench.glicko2 import (
    DEFAULT_RATING,
    DEFAULT_RD,
    DEFAULT_VOLATILITY,
    MAX_RD,
    PROVISIONAL_RD,
    Glicko2,
    Outcome,
    Rating,
)

__all__ = [
    "DEFAULT_RATING",
    "DEFAULT_RD",
    "DEFAULT_VOLATILITY",
    "MAX_RD",
    "PROVISIONAL_RD",
    "Glicko2",
    "Outcome",
    "Rating",
]

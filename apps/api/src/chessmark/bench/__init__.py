"""Rating and aggregate metrics.

Pure like `game/`: this package computes, it does not fetch. Nothing here imports `db`, `agents`,
or `api`, so the rating maths can be tested against hand-built games rather than a database.
"""

from chessmark.bench.bradley_terry import (
    CENTRE,
    PRIOR_DRAWS,
    PROVISIONAL_DEVIATION,
    Rating,
    fit,
    standing_key,
)

__all__ = [
    "CENTRE",
    "PRIOR_DRAWS",
    "PROVISIONAL_DEVIATION",
    "Rating",
    "fit",
    "standing_key",
]

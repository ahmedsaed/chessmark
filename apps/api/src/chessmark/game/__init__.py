"""The chess domain: rules, referee, and export.

Pure. This package must not import from `db`, `agents`, `api`, or `core` — it is the one part of
Chessmark that can be reasoned about with no knowledge of LLMs or infrastructure, and
`tests/game/test_purity.py` keeps it that way.
"""

from chessmark.game.board import BoardView, ChessBoard, LegalMove, Material
from chessmark.game.errors import (
    GameError,
    GameOverError,
    IllegalMoveError,
    IllegalMoveReason,
)
from chessmark.game.pgn import PgnMetadata, final_position_from_pgn, to_pgn
from chessmark.game.referee import (
    DEFAULT_MAX_PLIES,
    FORFEIT_TERMINATIONS,
    Colour,
    GameResult,
    MoveOutcome,
    Outcome,
    Referee,
    Termination,
)

__all__ = [
    "DEFAULT_MAX_PLIES",
    "FORFEIT_TERMINATIONS",
    "BoardView",
    "ChessBoard",
    "Colour",
    "GameError",
    "GameOverError",
    "GameResult",
    "IllegalMoveError",
    "IllegalMoveReason",
    "LegalMove",
    "Material",
    "MoveOutcome",
    "Outcome",
    "PgnMetadata",
    "Referee",
    "Termination",
    "final_position_from_pgn",
    "to_pgn",
]

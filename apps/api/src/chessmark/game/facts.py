"""What a position and each legal move *are*, worked out so a decision model does not have to.

A decision model reads a position and answers a typed question; it has no scratchpad, cannot
count, and reads a FEN string as text (ADR-0049). So what a board shows at a glance — which piece
moves, from where to where, what stands on the square it lands on — is spelled out here, and the
model is left with everything else: judging which move is best.

**What a board shows, and nothing a board does not** — the line ADR-0040 drew for the chat seats'
`get_legal_moves`, now drawn identically here. A capture is an occupied destination square, a
promotion and a castle are the move itself, a repeated position is a lookup in the game's own
history. Anything that has to be *worked out* from the pieces is left for the model to work out:

* **no mate** — trying every reply is the search being measured;
* **no check** — tracing lines from the moved piece to the king;
* **no attacks or defences** — which squares each side covers, which pieces hang. All three were
  here in the first draft and were removed on the owner's call: a fact on some moves and not
  others pulls a model toward or away from them, and none of it is on a chat model's board, so it
  made the two kinds of model play different games (ADR-0049).

Pure, like the rest of `game/`: no wording lives here. How these facts are phrased is part of the
decision harness's version and belongs to `agents/decision_request.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from chessmark.game.board import PIECE_VALUES, plain_san

_E_FILE = chess.FILE_NAMES.index("e")

PIECE_NAMES: dict[chess.PieceType, str] = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


@dataclass(frozen=True, slots=True)
class MoveFacts:
    """One legal move, and what playing it does — before anyone replies."""

    #: SAN without its `+`/`#` suffix, for the reason `plain_san` gives: the suffix is analysis.
    san: str
    uci: str
    piece: str
    from_square: str
    to_square: str
    #: The piece taken, including the pawn an en-passant capture removes from another square.
    captures: str | None
    promotes_to: str | None
    #: `"kingside"` or `"queenside"`.
    castles: str | None
    #: Whether the move recreates a position that has already occurred in this game. A lookup in
    #: the game's own history rather than a search, and the one fact that lets a seat steer toward
    #: or away from the repetition draws it is told about.
    repeats_position: bool = False


@dataclass(frozen=True, slots=True)
class PositionFacts:
    side_to_move: str
    in_check: bool
    #: Every piece on the board, by colour, as `"knight on g1"`, in square order.
    white_pieces: tuple[str, ...]
    black_pieces: tuple[str, ...]
    #: Conventional point totals — the same figure `get_board` gives a chat model.
    white_material: int
    black_material: int


def _colour(colour: chess.Color) -> str:
    return "white" if colour == chess.WHITE else "black"


def move_facts(board: chess.Board, move: chess.Move) -> MoveFacts:
    """The facts of one legal move. `board` is left exactly as it was found."""
    piece = board.piece_at(move.from_square)
    if piece is None:  # pragma: no cover - a legal move always starts on a piece
        raise ValueError(f"{move.uci()} does not start on a piece")

    if board.is_en_passant(move):
        captured: chess.PieceType | None = chess.PAWN
    else:
        target = board.piece_at(move.to_square)
        captured = target.piece_type if target is not None else None

    castles = None
    if board.is_castling(move):
        # The king lands on the g-file castling short and the c-file castling long.
        castles = "kingside" if chess.square_file(move.to_square) > _E_FILE else "queenside"

    san = plain_san(board.san(move))

    board.push(move)
    try:
        repeats_position = board.is_repetition(2)
    finally:
        board.pop()

    return MoveFacts(
        san=san,
        uci=move.uci(),
        piece=PIECE_NAMES[piece.piece_type],
        from_square=chess.square_name(move.from_square),
        to_square=chess.square_name(move.to_square),
        captures=PIECE_NAMES[captured] if captured is not None else None,
        promotes_to=PIECE_NAMES[move.promotion] if move.promotion else None,
        castles=castles,
        repeats_position=repeats_position,
    )


def legal_move_facts(board: chess.Board) -> list[MoveFacts]:
    """Every legal move's facts, sorted by SAN so the same position always reads the same way.

    Sorted rather than left in generation order because the order is part of what the model is
    shown, and python-chess's generation order is an implementation detail that could change
    between releases and move a probability with it.
    """
    return sorted((move_facts(board, move) for move in board.legal_moves), key=lambda m: m.san)


def _pieces(board: chess.Board, colour: chess.Color) -> tuple[str, ...]:
    return tuple(
        f"{PIECE_NAMES[piece.piece_type]} on {chess.square_name(square)}"
        for square, piece in sorted(board.piece_map().items())
        if piece.color == colour
    )


def _material(board: chess.Board, colour: chess.Color) -> int:
    return sum(
        len(board.pieces(piece_type, colour)) * value for piece_type, value in PIECE_VALUES.items()
    )


def position_facts(board: chess.Board) -> PositionFacts:
    return PositionFacts(
        side_to_move=_colour(board.turn),
        in_check=board.is_check(),
        white_pieces=_pieces(board, chess.WHITE),
        black_pieces=_pieces(board, chess.BLACK),
        white_material=_material(board, chess.WHITE),
        black_material=_material(board, chess.BLACK),
    )


__all__ = [
    "PIECE_NAMES",
    "MoveFacts",
    "PositionFacts",
    "legal_move_facts",
    "move_facts",
    "position_facts",
]

"""The authoritative chess position.

Everything an agent can learn about the board, and the single place a move can change it.
Models propose; `python-chess` disposes (ADR-0007 / invariant 1). This module knows nothing about
LLMs, databases, or HTTP, and must stay that way — `tests/game/test_purity.py` enforces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import chess

from chessmark.game.errors import IllegalMoveError, IllegalMoveReason

#: Conventional point values, used only for the material summary shown to agents.
PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

_CHECK_SUFFIXES = "+#!?"


@dataclass(frozen=True, slots=True)
class LegalMove:
    """One playable move, described in both notations an agent might use."""

    san: str
    uci: str
    is_capture: bool
    is_check: bool
    is_checkmate: bool
    is_castling: bool
    is_en_passant: bool
    promotion: str | None


@dataclass(frozen=True, slots=True)
class Material:
    """Point-value totals. `balance` is positive when White is ahead."""

    white: int
    black: int
    balance: int


@dataclass(frozen=True, slots=True)
class BoardView:
    """A complete, agent-facing snapshot of the position.

    This is what the `get_board` tool returns, so every field here is something we have decided
    a model is entitled to know.
    """

    fen: str
    ascii: str
    side_to_move: str
    fullmove_number: int
    halfmove_clock: int
    castling_rights: str
    en_passant: str | None
    in_check: bool
    material: Material
    legal_move_count: int

    #: How many times the current position has already occurred, this one included. At 3 the game
    #: is drawn automatically.
    #:
    #: **A raw `halfmove_clock` was the only hint about either draw rule, and it is not a hint.**
    #: A model would have to know the FEN convention, know the threshold is 100 rather than 50, and
    #: infer that we apply the rule at all — none of which was stated anywhere it could read. One
    #: game was drawn by threefold at ply 100 with a model a queen and a knight up, chasing the
    #: king with checks, having never been told the rule existed. Reported plainly instead.
    repetition_count: int = 1

    #: Plies until the fifty-move rule draws the game, from `halfmove_clock`. Named for the
    #: consequence rather than the counter, because the counter is what nobody read.
    plies_until_fifty_move_draw: int = 100

    move_history_san: list[str] = field(default_factory=list)


def _destination_square(san: str) -> int | None:
    """The square a piece of algebraic notation lands on, or None if it names none.

    Deliberately forgiving: it is reading a move somebody already got wrong, so `e8+`, `Qe8!?` and
    `exd8` all have to give up their last file-and-rank rather than be parsed properly.
    """
    match = re.search(r"([a-h][1-8])(?!.*[a-h][1-8])", san)
    if match is None:
        return None
    return chess.parse_square(match.group(1))


def _colour_name(colour: chess.Color) -> str:
    return "white" if colour == chess.WHITE else "black"


def _normalise_san(san: str) -> str:
    """Strip check, mate, and annotation suffixes so notations compare equal."""
    return san.rstrip(_CHECK_SUFFIXES)


class ChessBoard:
    """Mutable wrapper over `chess.Board` with agent-facing views and explanatory failures."""

    def __init__(self, fen: str | None = None) -> None:
        self._board = chess.Board(fen) if fen else chess.Board()
        self._root_fen = self._board.fen()
        self._history_san: list[str] = []

    # ------------------------------------------------------------------ state

    @property
    def fen(self) -> str:
        return self._board.fen()

    @property
    def root_fen(self) -> str:
        """The position this board started from. Needed to replay or export the game."""
        return self._root_fen

    @property
    def side_to_move(self) -> str:
        return _colour_name(self._board.turn)

    @property
    def ply(self) -> int:
        """Half-moves played on this board since it was created."""
        return len(self._board.move_stack)

    @property
    def in_check(self) -> bool:
        return self._board.is_check()

    def history_san(self) -> list[str]:
        return list(self._history_san)

    def copy(self) -> ChessBoard:
        clone = ChessBoard(self._root_fen)
        clone._board = self._board.copy()
        clone._history_san = list(self._history_san)
        return clone

    # ------------------------------------------------------------------ views

    def legal_moves(self) -> list[LegalMove]:
        """Every legal move, sorted by SAN so the list is stable across calls."""
        moves = [self._describe(move) for move in self._board.legal_moves]
        return sorted(moves, key=lambda m: m.san)

    def legal_moves_san(self) -> list[str]:
        return [move.san for move in self.legal_moves()]

    def material(self) -> Material:
        white = black = 0
        for piece_type, value in PIECE_VALUES.items():
            white += len(self._board.pieces(piece_type, chess.WHITE)) * value
            black += len(self._board.pieces(piece_type, chess.BLACK)) * value
        return Material(white=white, black=black, balance=white - black)

    def ascii(self, *, perspective: str = "white") -> str:
        """A labelled ASCII diagram.

        Uppercase is White, lowercase is Black, `.` is empty. This goes straight into an LLM
        prompt, so the rank and file labels are worth the extra characters.
        """
        ranks = range(7, -1, -1) if perspective == "white" else range(8)
        files = range(8) if perspective == "white" else range(7, -1, -1)
        header = "  " + " ".join(chess.FILE_NAMES[f] for f in files)

        lines = [header]
        for rank in ranks:
            cells = []
            for file in files:
                piece = self._board.piece_at(chess.square(file, rank))
                cells.append(piece.symbol() if piece else ".")
            label = str(rank + 1)
            lines.append(f"{label} {' '.join(cells)} {label}")
        lines.append(header)
        return "\n".join(lines)

    def view(self, *, perspective: str | None = None) -> BoardView:
        board = self._board
        ep = board.ep_square
        fen = board.fen()
        return BoardView(
            fen=fen,
            ascii=self.ascii(perspective=perspective or self.side_to_move),
            side_to_move=self.side_to_move,
            fullmove_number=board.fullmove_number,
            halfmove_clock=board.halfmove_clock,
            castling_rights=fen.split()[2],
            en_passant=chess.square_name(ep) if ep is not None else None,
            in_check=board.is_check(),
            material=self.material(),
            legal_move_count=board.legal_moves.count(),
            repetition_count=self.repetition_count(),
            plies_until_fifty_move_draw=max(0, 100 - board.halfmove_clock),
            move_history_san=self.history_san(),
        )

    # ------------------------------------------------------------------ moves

    def parse(self, text: str) -> chess.Move:
        """Resolve SAN or UCI to a legal move, or raise an explanatory error."""
        cleaned = text.strip()
        if not cleaned:
            raise self._reject(text, IllegalMoveReason.INVALID_NOTATION, "no move was supplied")

        well_formed_san = False
        try:
            return self._board.parse_san(cleaned)
        except chess.AmbiguousMoveError:
            raise self._reject(
                cleaned,
                IllegalMoveReason.AMBIGUOUS,
                f"{cleaned!r} is ambiguous — more than one piece can play it. "
                "Disambiguate by file or rank, for example Nbd2 or R1e2.",
            ) from None
        except chess.IllegalMoveError:
            well_formed_san = True
        except chess.InvalidMoveError:
            pass

        try:
            move = chess.Move.from_uci(cleaned.lower())
        except chess.InvalidMoveError:
            if well_formed_san:
                raise self._explain_san(cleaned) from None
            raise self._reject(
                cleaned,
                IllegalMoveReason.INVALID_NOTATION,
                f"{cleaned!r} is neither algebraic notation (e4, Nf3, O-O, exd5, e8=Q) "
                "nor UCI notation (e2e4, g1f3, e7e8q).",
            ) from None

        if move in self._board.legal_moves:
            return move
        raise self._explain_move(cleaned, move)

    def push(self, text: str) -> LegalMove:
        """Validate and apply a move, returning its description."""
        move = self.parse(text)
        described = self._describe(move)
        self._history_san.append(described.san)
        self._board.push(move)
        return described

    # ------------------------------------------------------------------ rules

    def is_checkmate(self) -> bool:
        return self._board.is_checkmate()

    def is_stalemate(self) -> bool:
        return self._board.is_stalemate()

    def is_insufficient_material(self) -> bool:
        return self._board.is_insufficient_material()

    def repetition_count(self) -> int:
        """How many times this exact position has occurred, including now.

        Counts occurrences anywhere in the game — repetition is **not** required to be sequential
        (FIDE 9.2.1 says so in parentheses, because it is the rule's usual misreading). And what
        must match is the *position*: piece placement, side to move, castling rights and en-passant
        availability. A rook that returns to its square having lost the right to castle has not
        repeated anything.

        `python-chess` has `is_repetition(n)` but no count, so this asks upward and stops at the
        threshold — three is where it matters and a higher number tells a reader nothing more.
        """
        for count in (3, 2):
            if self._board.is_repetition(count):
                return count
        return 1

    def is_threefold_repetition(self) -> bool:
        return self._board.is_repetition(3)

    def is_fifty_move_rule(self) -> bool:
        return self._board.halfmove_clock >= 100

    def is_fivefold_repetition(self) -> bool:
        """FIDE 9.6.2 — the hard backstop, needing no claim from anybody."""
        return self._board.is_repetition(5)

    def is_seventy_five_move_rule(self) -> bool:
        """FIDE 9.6.1 — likewise. 150 plies, since the clock counts half-moves."""
        return self._board.halfmove_clock >= 150

    @property
    def raw(self) -> chess.Board:
        """Escape hatch for PGN export and engine analysis. Treat as read-only."""
        return self._board

    # ------------------------------------------------------------------ internals

    def _describe(self, move: chess.Move) -> LegalMove:
        board = self._board
        san = board.san(move)
        is_capture = board.is_capture(move)
        is_en_passant = board.is_en_passant(move)
        is_castling = board.is_castling(move)

        board.push(move)
        try:
            is_check = board.is_check()
            is_checkmate = board.is_checkmate()
        finally:
            board.pop()

        promotion = chess.piece_symbol(move.promotion).upper() if move.promotion else None

        return LegalMove(
            san=san,
            uci=move.uci(),
            is_capture=is_capture,
            is_check=is_check,
            is_checkmate=is_checkmate,
            is_castling=is_castling,
            is_en_passant=is_en_passant,
            promotion=promotion,
        )

    def _reject(self, move: str, reason: IllegalMoveReason, detail: str) -> IllegalMoveError:
        return IllegalMoveError(
            move=move,
            reason=reason,
            detail=detail,
            fen=self.fen,
            legal_moves_san=self.legal_moves_san(),
        )

    def _promotion_options(self, to_square: int, from_square: int | None = None) -> list[str]:
        """Which pieces a pawn may become on this square, if any legal move promotes there."""
        return sorted(
            {
                chess.piece_symbol(legal.promotion).upper()
                for legal in self._board.legal_moves
                if legal.promotion
                and legal.to_square == to_square
                and (from_square is None or legal.from_square == from_square)
            }
        )

    def _missing_promotion(
        self, text: str, destination: str, options: list[str]
    ) -> IllegalMoveError:
        """The move is right; only the piece it becomes is missing.

        Its own reason and its own sentence, because every other explanation here would be false:
        the pawn *can* go there. `NOT_REACHABLE` told a model that had found the move that its pawn
        could not make it, and charged it an illegal attempt for the privilege.

        Queen leads the UCI example — under-promotion is the rarity, and the example should show
        the answer somebody most likely wants.
        """
        listed = ", ".join(f"{destination}={piece}" for piece in options)
        uci = "q" if "Q" in options else options[0].lower()
        return self._reject(
            text,
            IllegalMoveReason.MISSING_PROMOTION,
            f"{text} reaches the last rank — say which piece the pawn becomes: {listed} "
            f"(or append {uci!r} in UCI).",
        )

    def _explain_san(self, san: str) -> IllegalMoveError:
        """Explain a well-formed algebraic move that isn't legal here.

        The most useful case to catch is a move that is mechanically fine but leaves the king in
        check — the model isn't confused about how the piece moves, it has missed a pin.
        """
        target = _normalise_san(san)
        for pseudo in self._board.generate_pseudo_legal_moves():
            if _normalise_san(self._board.san(pseudo)) != target:
                continue
            if self._board.is_into_check(pseudo):
                return self._reject(
                    san,
                    IllegalMoveReason.LEAVES_KING_IN_CHECK,
                    f"{san} would leave your king in check.",
                )
            break

        # A pawn move written without the piece it becomes — `e8` rather than `e8=Q`. Checked
        # here rather than left to the generic answer, which would deny a move the player found.
        square = _destination_square(san)
        if square is not None:
            options = self._promotion_options(square)
            if options:
                return self._missing_promotion(san, chess.square_name(square), options)

        return self._reject(
            san,
            IllegalMoveReason.NOT_REACHABLE,
            f"no legal move in this position matches {san!r}.",
        )

    def _explain_move(self, text: str, move: chess.Move) -> IllegalMoveError:
        """Explain a UCI move that parsed but isn't legal."""
        board = self._board
        origin = chess.square_name(move.from_square)
        destination = chess.square_name(move.to_square)
        piece = board.piece_at(move.from_square)

        if piece is None:
            return self._reject(text, IllegalMoveReason.NO_PIECE, f"there is no piece on {origin}.")

        name = chess.piece_name(piece.piece_type)
        if piece.color != board.turn:
            return self._reject(
                text,
                IllegalMoveReason.WRONG_COLOR,
                f"the {name} on {origin} is {_colour_name(piece.color).capitalize()}'s, "
                f"and it is {self.side_to_move.capitalize()}'s turn.",
            )

        # A pawn arriving on the last rank without saying what it becomes. Checked before the
        # generic explanations, because every one of them would be wrong: the move is legal apart
        # from the missing qualifier, and the player has already found it.
        if move.promotion is None:
            options = self._promotion_options(move.to_square, move.from_square)
            if options:
                return self._missing_promotion(text, destination, options)

        if move in board.generate_pseudo_legal_moves() and board.is_into_check(move):
            return self._reject(
                text,
                IllegalMoveReason.LEAVES_KING_IN_CHECK,
                f"moving the {name} from {origin} to {destination} would leave your king in check.",
            )

        return self._reject(
            text,
            IllegalMoveReason.NOT_REACHABLE,
            f"the {name} on {origin} cannot move to {destination}.",
        )

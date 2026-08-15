"""The seven tools an agent can call, and their dispatch against the authoritative board.

AGENT-01: agents act *only* through tools. There is no free-text move parsing anywhere in
Chessmark — a model that describes its move in prose has not moved.

Tools are pure functions of the server-side `Referee`. `get_board`, `get_legal_moves`, and
`get_move_history` read; `make_move`, `resign`, and `offer_draw` mutate; `say` touches the game
not at all. A model can never corrupt the record, only propose to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chessmark.agents.types import ToolInvocation
from chessmark.game import Colour, IllegalMoveError, MoveOutcome, Referee, Termination

#: Bumped whenever a tool's name, arguments, or semantics change. Recorded on every game, because
#: results produced under different tool surfaces are not comparable (BENCH-04).
TOOL_SCHEMA_VERSION = "v1"

MAX_MESSAGE_LENGTH = 280
MAX_MESSAGES_PER_TURN = 3


class ToolName:
    GET_BOARD = "get_board"
    GET_LEGAL_MOVES = "get_legal_moves"
    GET_MOVE_HISTORY = "get_move_history"
    MAKE_MOVE = "make_move"
    SAY = "say"
    OFFER_DRAW = "offer_draw"
    RESIGN = "resign"


READ_ONLY_TOOLS = frozenset(
    {ToolName.GET_BOARD, ToolName.GET_LEGAL_MOVES, ToolName.GET_MOVE_HISTORY}
)


def _fn(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


def tool_schemas(*, trash_talk_enabled: bool = True) -> list[dict[str, Any]]:
    """The tool list sent to the provider.

    Part of the cached prefix, so for a given game this must return the same thing every turn.
    """
    schemas = [
        _fn(
            ToolName.GET_BOARD,
            "Get the current position: FEN, a board diagram, whose turn it is, castling rights, "
            "material balance, and whether you are in check. Free to call.",
            {},
        ),
        _fn(
            ToolName.GET_LEGAL_MOVES,
            "List every legal move in the current position, in algebraic and UCI notation, with "
            "flags for captures, checks, and promotions. Free to call, and the reliable way to "
            "avoid an illegal move.",
            {},
        ),
        _fn(
            ToolName.GET_MOVE_HISTORY,
            "The moves played so far, in order. Free to call.",
            {
                "last_n": {
                    "type": "integer",
                    "description": "Return only the most recent N moves. Omit for the whole game.",
                }
            },
        ),
        _fn(
            ToolName.MAKE_MOVE,
            "Play a move. This is the only way to move. If the move is illegal it is rejected "
            "with an explanation and the full list of legal moves, and you may try again.",
            {
                "move": {
                    "type": "string",
                    "description": "Algebraic (e4, Nf3, O-O, exd5, e8=Q) or UCI (e2e4, e7e8q).",
                }
            },
            ["move"],
        ),
        _fn(
            ToolName.OFFER_DRAW,
            "Offer your opponent a draw.",
            {},
        ),
        _fn(
            ToolName.RESIGN,
            "Resign the game. This is final and you lose immediately.",
            {},
        ),
    ]

    if trash_talk_enabled:
        schemas.append(
            _fn(
                ToolName.SAY,
                "Say something to your opponent. Shown live to spectators. You may call this "
                f"up to {MAX_MESSAGES_PER_TURN} times per turn, or not at all.",
                {
                    "message": {
                        "type": "string",
                        "description": f"What to say. At most {MAX_MESSAGE_LENGTH} characters.",
                    }
                },
                ["message"],
            )
        )

    return schemas


@dataclass(slots=True)
class ToolResult:
    """What a tool returned, plus what it did to the game."""

    payload: dict[str, Any]
    ok: bool = True
    move: MoveOutcome | None = None
    """Set when a move was committed. The turn ends."""

    illegal: bool = False
    """Set when `make_move` was rejected. Counts against the retry budget (ADR-0002)."""

    message: str | None = None
    """Set when `say` produced a message to broadcast."""

    ends_turn: bool = False
    ends_game: bool = False


@dataclass(slots=True)
class TurnState:
    """Mutable state for the turn in progress."""

    illegal_attempts: int = 0
    messages_sent: int = 0
    tool_calls: int = 0
    said: list[str] = field(default_factory=list)


class ToolDispatcher:
    """Executes tool calls against the authoritative referee."""

    def __init__(
        self,
        *,
        referee: Referee,
        colour: Colour,
        state: TurnState,
        max_illegal_retries: int = 5,
        trash_talk_enabled: bool = True,
    ) -> None:
        self.referee = referee
        self.colour = colour
        self.state = state
        self.max_illegal_retries = max_illegal_retries
        self.trash_talk_enabled = trash_talk_enabled

    # ------------------------------------------------------------------ dispatch

    def execute(self, call: ToolInvocation) -> ToolResult:
        if not call.ok:
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "invalid_arguments",
                    "detail": (
                        f"Could not read the arguments to {call.name}: {call.parse_error}. "
                        "Arguments must be a JSON object."
                    ),
                },
                ok=False,
            )

        handler = {
            ToolName.GET_BOARD: self._get_board,
            ToolName.GET_LEGAL_MOVES: self._get_legal_moves,
            ToolName.GET_MOVE_HISTORY: self._get_move_history,
            ToolName.MAKE_MOVE: self._make_move,
            ToolName.SAY: self._say,
            ToolName.OFFER_DRAW: self._offer_draw,
            ToolName.RESIGN: self._resign,
        }.get(call.name)

        if handler is None:
            available = ", ".join(sorted(self._available_tool_names()))
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "unknown_tool",
                    "detail": f"There is no tool called {call.name!r}. Available: {available}.",
                },
                ok=False,
            )

        self.state.tool_calls += 1
        return handler(call.arguments)

    def _available_tool_names(self) -> set[str]:
        names = {
            ToolName.GET_BOARD,
            ToolName.GET_LEGAL_MOVES,
            ToolName.GET_MOVE_HISTORY,
            ToolName.MAKE_MOVE,
            ToolName.OFFER_DRAW,
            ToolName.RESIGN,
        }
        if self.trash_talk_enabled:
            names.add(ToolName.SAY)
        return names

    # ------------------------------------------------------------------ read-only

    def _get_board(self, _arguments: dict[str, Any]) -> ToolResult:
        view = self.referee.board.view(perspective=self.colour.value)
        return ToolResult(
            payload={
                "ok": True,
                "fen": view.fen,
                "board": view.ascii,
                "side_to_move": view.side_to_move,
                "you_are": self.colour.value,
                "move_number": view.fullmove_number,
                "in_check": view.in_check,
                "castling_rights": view.castling_rights,
                "en_passant": view.en_passant,
                "material": {
                    "white": view.material.white,
                    "black": view.material.black,
                    "balance": view.material.balance,
                },
                "legal_move_count": view.legal_move_count,
                "halfmove_clock": view.halfmove_clock,
            }
        )

    def _get_legal_moves(self, _arguments: dict[str, Any]) -> ToolResult:
        moves = self.referee.board.legal_moves()
        return ToolResult(
            payload={
                "ok": True,
                "count": len(moves),
                "moves": [
                    {
                        "san": move.san,
                        "uci": move.uci,
                        **({"capture": True} if move.is_capture else {}),
                        **({"check": True} if move.is_check else {}),
                        **({"checkmate": True} if move.is_checkmate else {}),
                        **({"promotion": move.promotion} if move.promotion else {}),
                    }
                    for move in moves
                ],
            }
        )

    def _get_move_history(self, arguments: dict[str, Any]) -> ToolResult:
        history = self.referee.board.history_san()
        last_n = arguments.get("last_n")
        if isinstance(last_n, int) and last_n > 0:
            history = history[-last_n:]

        return ToolResult(payload={"ok": True, "ply_count": self.referee.ply, "moves": history})

    # ------------------------------------------------------------------ mutating

    def _make_move(self, arguments: dict[str, Any]) -> ToolResult:
        raw = arguments.get("move")
        if not isinstance(raw, str):
            self.state.illegal_attempts += 1
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "invalid_arguments",
                    "detail": 'make_move requires a string `move`, for example {"move": "e4"}.',
                    "attempt": self.state.illegal_attempts,
                    "attempts_remaining": self._attempts_remaining(),
                    "fen": self.referee.board.fen,
                    "legal_moves_san": self.referee.board.legal_moves_san(),
                },
                ok=False,
                illegal=True,
            )

        try:
            outcome = self.referee.play(raw)
        except IllegalMoveError as error:
            self.state.illegal_attempts += 1
            # ADR-0002: the rejection carries everything needed to recover. The benchmark measures
            # whether a model can act correctly given complete information, not whether it guesses.
            rejection = error.as_dict()
            rejection["attempt"] = self.state.illegal_attempts
            rejection["attempts_remaining"] = self._attempts_remaining()
            return ToolResult(payload=rejection, ok=False, illegal=True)

        payload: dict[str, Any] = {
            "ok": True,
            "played": outcome.move.san,
            "uci": outcome.move.uci,
            "fen": outcome.fen_after,
            "ply": outcome.ply,
        }
        if outcome.move.is_check:
            payload["check"] = True
        if outcome.outcome is not None:
            payload["game_over"] = True
            payload["result"] = str(outcome.outcome.result)
            payload["termination"] = str(outcome.outcome.termination)
            payload["detail"] = outcome.outcome.detail

        return ToolResult(
            payload=payload,
            move=outcome,
            ends_turn=True,
            ends_game=outcome.outcome is not None,
        )

    def _resign(self, _arguments: dict[str, Any]) -> ToolResult:
        outcome = self.referee.resign(self.colour)
        return ToolResult(
            payload={
                "ok": True,
                "resigned": True,
                "result": str(outcome.result),
                "detail": outcome.detail,
            },
            ends_turn=True,
            ends_game=True,
        )

    def _offer_draw(self, _arguments: dict[str, Any]) -> ToolResult:
        # The opponent answers on its own turn; nothing changes yet.
        return ToolResult(
            payload={
                "ok": True,
                "offered": True,
                "detail": "Draw offered. Your opponent will respond on its turn. "
                "You must still make a move now.",
            }
        )

    # ------------------------------------------------------------------ talking

    def _say(self, arguments: dict[str, Any]) -> ToolResult:
        if not self.trash_talk_enabled:
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "disabled",
                    "detail": "This is a ranked game; `say` is disabled.",
                },
                ok=False,
            )

        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "invalid_arguments",
                    "detail": "say requires a non-empty string `message`.",
                },
                ok=False,
            )

        if len(message) > MAX_MESSAGE_LENGTH:
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "too_long",
                    "detail": f"Message was {len(message)} characters; the limit is "
                    f"{MAX_MESSAGE_LENGTH}. Say less.",
                },
                ok=False,
            )

        if self.state.messages_sent >= MAX_MESSAGES_PER_TURN:
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "rate_limited",
                    "detail": f"You have already spoken {MAX_MESSAGES_PER_TURN} times this turn. "
                    "Make your move.",
                },
                ok=False,
            )

        cleaned = message.strip()
        self.state.messages_sent += 1
        self.state.said.append(cleaned)
        return ToolResult(payload={"ok": True, "said": cleaned}, message=cleaned)

    # ------------------------------------------------------------------ helpers

    def _attempts_remaining(self) -> int:
        """Failures still survivable. At zero, the next illegal move forfeits the game."""
        return max(self.max_illegal_retries - self.state.illegal_attempts, 0)

    @property
    def retries_exhausted(self) -> bool:
        """`max_illegal_retries` is the number of failures *tolerated*, so the next one is fatal.

        With the default of 5: five illegal moves followed by a legal one is a completed turn with
        `illegal_attempts = 5`; a sixth failure forfeits.
        """
        return self.state.illegal_attempts > self.max_illegal_retries


__all__ = [
    "MAX_MESSAGES_PER_TURN",
    "MAX_MESSAGE_LENGTH",
    "READ_ONLY_TOOLS",
    "TOOL_SCHEMA_VERSION",
    "Termination",
    "ToolDispatcher",
    "ToolName",
    "ToolResult",
    "TurnState",
    "tool_schemas",
]

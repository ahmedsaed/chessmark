"""The seven tools an agent can call, and their dispatch against the authoritative board.

AGENT-01: agents act *only* through tools. There is no free-text move parsing anywhere in
Chessmark — a model that describes its move in prose has not moved.

Tools are pure functions of the server-side `Referee`. `get_board`, `get_legal_moves`, and
`get_move_history` read; `make_move`, `resign`, and `offer_draw` mutate; `say` touches the game
not at all. A model can never corrupt the record, only propose to it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from chessmark.agents.types import ToolInvocation
from chessmark.game import (
    Colour,
    DrawNotClaimableError,
    IllegalMoveError,
    MoveOutcome,
    Referee,
    Termination,
    plain_san,
)

#: Bumped whenever a tool's name, arguments, or semantics change. Recorded on every game, because
#: results produced under different tool surfaces are not comparable (BENCH-04).
#: Bumped to v2 on 2026-08-28, adding `claim_draw`. The schema is part of the cached prefix, so it
#: cannot vary within a game — a new tool is a new version by construction.
#: v3 on 2026-09-13 with `accept_draw`, and the `check`/`checkmate` flags out of `get_legal_moves`
#: (ADR-0040).
#:
#: **v4 on 2026-09-14, and it is the first bump that had to stand on its own** (ADR-0042). v3 took
#: the flags out and left the same fact in the notation, so the list still came back naming the
#: mating move — `Qxf7#`, one `#` among forty-five alphabetically sorted moves, and the model
#: played it. Stripping the suffix removes information without touching a word of the prompt,
#: which is why `judge` now reads this version too: until today it was recorded and never checked,
#: and every previous tool change happened to move `PROMPT_VERSION` alongside it.
TOOL_SCHEMA_VERSION = "v4"

MAX_MESSAGE_LENGTH = 280
MAX_MESSAGES_PER_TURN = 3


class ToolName(StrEnum):
    """The tool names, as an enum so the set can be *iterated* rather than restated.

    It was a plain class of string constants, and the test that checked the full surface listed
    them again by hand — under the name `test_all_seven_tools_are_offered`, which adding an eighth
    made false. A `StrEnum` compares and serialises exactly like the strings it replaces.
    """

    GET_BOARD = "get_board"
    GET_LEGAL_MOVES = "get_legal_moves"
    GET_MOVE_HISTORY = "get_move_history"
    MAKE_MOVE = "make_move"
    SAY = "say"
    OFFER_DRAW = "offer_draw"
    ACCEPT_DRAW = "accept_draw"
    CLAIM_DRAW = "claim_draw"
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
            "List every legal move in the current position, in algebraic and UCI notation, "
            "marking captures and promotions. Free to call, and the reliable way to avoid an "
            "illegal move.",
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
            "Offer your opponent a draw. The offer reaches them at the start of their next turn, "
            "and stands until they answer it or move. You must still make your own move now.",
            {},
        ),
        _fn(
            ToolName.ACCEPT_DRAW,
            "Accept a draw your opponent has offered, ending the game immediately as a draw. "
            "Only call this when you have been told an offer is open — otherwise it is refused, "
            "harmlessly, and you play on.",
            {},
        ),
        _fn(
            ToolName.CLAIM_DRAW,
            "Claim a draw by threefold repetition or the fifty-move rule. Neither is applied "
            "automatically — you must claim it. Succeeds and ends the game immediately if the "
            "position has occurred three times, or if fifty moves have passed with no capture and "
            "no pawn move; otherwise it is refused and tells you how far off each one is, and you "
            "play on as normal. A refused claim costs you nothing.",
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

    offers_draw: bool = False
    """Set by `offer_draw`. The dispatcher is synchronous and holds no session, so recording the
    offer and delivering it to the opponent is the turn loop's job — the same split `move` already
    uses. Before ADR-0040 nothing did it at all: `offer_draw` returned "Draw offered" to the seat
    that called it and the opponent was never told, in every model-vs-model game ever played."""


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
        draw_offered: bool = False,
    ) -> None:
        self.referee = referee
        self.colour = colour
        self.state = state
        self.max_illegal_retries = max_illegal_retries
        self.trash_talk_enabled = trash_talk_enabled
        #: Whether the opponent has an offer standing against this seat, read from `game_events`
        #: once at the top of the turn. Passed in rather than looked up here because this class is
        #: synchronous and deliberately knows nothing but the referee.
        self.draw_offered = draw_offered

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

        handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            ToolName.GET_BOARD: self._get_board,
            ToolName.GET_LEGAL_MOVES: self._get_legal_moves,
            ToolName.GET_MOVE_HISTORY: self._get_move_history,
            ToolName.MAKE_MOVE: self._make_move,
            ToolName.SAY: self._say,
            ToolName.OFFER_DRAW: self._offer_draw,
            ToolName.ACCEPT_DRAW: self._accept_draw,
            ToolName.CLAIM_DRAW: self._claim_draw,
            ToolName.RESIGN: self._resign,
        }
        handler = handlers.get(call.name)

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
        """Derived from the enum, not restated.

        This was a second hand-maintained copy of the tool list, and adding `claim_draw` left it
        behind: the unknown-tool message would have told a model that the tool it had just been
        offered did not exist. `say` is the only conditional one.
        """
        names = {str(name) for name in ToolName}
        if not self.trash_talk_enabled:
            names.discard(str(ToolName.SAY))
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
                # Both draw rules, named for their consequence. `halfmove_clock` stays because it
                # is part of the FEN a model may be reconciling against, but it was never a
                # disclosure: a bare integer does not say what it counts or what happens at 100.
                "repetition_count": view.repetition_count,
                "plies_until_fifty_move_draw": view.plies_until_fifty_move_draw,
            }
        )

    def _offered_moves(self) -> list[str]:
        """The legal move list exactly as a model is shown it.

        **One function, because there are two ways to ask for this list and they must agree**
        (ADR-0042). `get_legal_moves` is the obvious one; the other is an illegal move, which
        ADR-0002 answers with the full list so a model can always recover. Stripping the suffix
        from one and not the other would mean a model could read the mate off the board by playing
        something illegal first, which is a worse surface than the one ADR-0040 set out to close.
        """
        return [plain_san(san) for san in self.referee.board.legal_moves_san()]

    def _get_legal_moves(self, _arguments: dict[str, Any]) -> ToolResult:
        moves = self.referee.board.legal_moves()
        return ToolResult(
            payload={
                "ok": True,
                "count": len(moves),
                # **What a board shows, and nothing a board does not** (ADR-0040).
                #
                # `check` and `checkmate` used to be here, and they were a one-ply search with
                # terminal evaluation handed over free on every move of every turn: no model ever
                # had to *find* mate in one, it was told. That is the single most decision-relevant
                # fact in chess, and flagging it compressed the gap between a strong model and a
                # weak one at exactly the moment a game is decided. `check` went with it because it
                # made the ADR-0020 shuffle — chasing a bare king with checks into a repetition —
                # trivial to find without seeing anything.
                #
                # A capture stays: every board client marks an occupied destination square
                # differently, and it is one glance at the position rather than a ply of search.
                # `promotion` stays because it is mechanical — the board asks you which piece.
                "moves": [
                    {
                        # **Stripped of its `+` and `#`** (ADR-0042). ADR-0040 removed the `check`
                        # and `checkmate` flags and left the same fact in the notation, where it is
                        # easier to find than the flag was: one real v3 list came back with 45
                        # moves in alphabetical order and a single `#` among them, and the model
                        # played it. A suffix is a courtesy annotation, not part of a move's
                        # identity — `parse` has always accepted `Nc3` for `Nc3#`.
                        "san": plain_san(move.san),
                        "uci": move.uci,
                        **({"capture": True} if move.is_capture else {}),
                        **({"promotion": move.promotion} if move.promotion else {}),
                    }
                    for move in moves
                ],
            }
        )

    def _get_move_history(self, arguments: dict[str, Any]) -> ToolResult:
        #: **The `+` and `#` stay here, and that is deliberate** — `history_san()`, not `plain_san`.
        #: The asymmetry with `get_legal_moves` is real and looks like an oversight, so: ADR-0042
        #: strips the suffix from the *legal move list* because that list is a search handed over
        #: free, and one `#` among forty-five alphabetically sorted moves names the mating move to a
        #: model that never had to find it. A history is the opposite — moves already played, on a
        #: board both seats can read, naming nothing about the position in front of them. Every PGN
        #: ever written carries these marks.
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
                    "legal_moves_san": self._offered_moves(),
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
            rejection["legal_moves_san"] = self._offered_moves()
            # The same list, through the same function: ADR-0002's recovery path must not be a way
            # around ADR-0040's disclosure line.
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
        # The opponent answers on its own turn; nothing about the position changes yet. What *does*
        # happen is `offers_draw`, which the turn loop turns into a `draw_offered` event and a line
        # in the opponent's next turn prompt.
        return ToolResult(
            payload={
                "ok": True,
                "offered": True,
                "detail": "Draw offered. Your opponent will be told at the start of its next turn "
                "and may accept or play on. You must still make a move now.",
            },
            offers_draw=True,
        )

    def _accept_draw(self, _arguments: dict[str, Any]) -> ToolResult:
        """Accept the opponent's standing offer, ending the game as a draw.

        **A refusal here is not an illegal move**, for the same reason `claim_draw`'s is not: a
        model asking whether an offer is open has broken no rule, and charging it against the
        retry budget would forfeit a seat for asking (ADR-0020).
        """
        if not self.draw_offered:
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "no_draw_offer",
                    "detail": "Your opponent has not offered a draw, so there is nothing to "
                    "accept. Play on as normal; this costs you nothing.",
                },
                ok=False,
            )

        outcome = self.referee.agree_draw()
        return ToolResult(
            payload={
                "ok": True,
                "accepted": True,
                "result": str(outcome.result),
                "detail": outcome.detail,
            },
            ends_turn=True,
            ends_game=True,
        )

    def _claim_draw(self, _arguments: dict[str, Any]) -> ToolResult:
        """A refused claim is `ok: False` but **not** an illegal move.

        `ok=False` is what marks a tool result as unsuccessful for the transcript; what matters is
        that this never reaches the illegal-move counter. A claim that does not apply is a question
        answered, not a rule broken, and charging it as an attempt would forfeit a model for asking
        — which is exactly the mistake `MISSING_PROMOTION` was created to stop making.
        """
        try:
            outcome = self.referee.claim_draw()
        except DrawNotClaimableError as refusal:
            view = self.referee.board.view()
            return ToolResult(
                payload={
                    "ok": False,
                    "error": "not_claimable",
                    "detail": str(refusal),
                    "repetition_count": refusal.repetition_count,
                    "repetitions_needed": 3,
                    "plies_until_fifty_move_draw": view.plies_until_fifty_move_draw,
                    "you_must_still_move": True,
                },
                ok=False,
            )

        return ToolResult(
            payload={
                "ok": True,
                "claimed": True,
                "result": str(outcome.result),
                "termination": str(outcome.termination),
                "detail": outcome.detail,
            },
            ends_turn=True,
            ends_game=True,
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

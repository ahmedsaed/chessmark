"""What a decision model is shown, and what it is asked (ADR-0049).

The decision harness's equivalent of `prompts.py` and `tools.py` together, and versioned the same
way for the same reason: results produced under different questions are not comparable (BENCH-04).
**Changing a word of the output — a fact added, a field renamed, a criterion reworded — is a bump
of `DECISION_VERSION`**, and `tests/agents/test_decision_request.py` holds a recorded request that
fails until the bump is made.

It has a version of its own, apart from `PROMPT_VERSION` and `TOOL_SCHEMA_VERSION`, because the two
harnesses change for different reasons and must be able to do so without retiring each other's
games: a new rule stated to the chat models is not a change to what a decision model is asked.

**Everything a chat seat can do on a turn, a decision seat is asked about**: its move, and whether
to resign, offer a draw, claim one, or accept one. The chat seat does these through tools; this seat
answers a `noul` for each, and code acts on the answers in a fixed order (`DecisionTurnRunner`).

**What a board shows, spelled out; nothing a board does not.** A decision model reads state as
data rather than as a board, so what a chat model sees at a glance — which piece a move uses, from
where to where, what stands where it lands, whether it repeats — is written out for it
(`game/facts.py`), and material is summed because it cannot add. Nothing that has to be worked out
is: no check, no mate, no attacks or defences. Those are the chess, and handing them to one kind
of model and not the other made the two play different games (ADR-0049).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chess

from chessmark.game.facts import (
    PIECE_NAMES,
    MoveFacts,
    legal_move_facts,
    position_facts,
)

#: Bumped whenever the request's state, questions or facts change. Majors mark a change of task and
#: split ratings and pool eras, exactly as `PROMPT_VERSION`'s do (`bench/ratable.same_task`).
#:
#: `d1` on 2026-09-26: the position, material and the recent moves as state; one `choice`
#: over the legal moves, each described; and a `noul` for every other thing a player may do on a
#: turn — resign and offer a draw always, claim a draw when one is claimable, accept one when it is
#: on offer. Everything a chat seat can do through its tools, a decision seat is asked about.
DECISION_VERSION = "d1"

#: The question keys. Ours, not the model's — the API never sends a key to the model, which is why
#: every instruction below carries its full meaning on its own.
MOVE_QUESTION = "move"
ACCEPT_DRAW_QUESTION = "accept_draw"
CLAIM_DRAW_QUESTION = "claim_draw"
OFFER_DRAW_QUESTION = "offer_draw"
RESIGN_QUESTION = "resign"

#: How many plies of history the state carries. Enough to see the shape of the last few moves and
#: any repetition building; a whole 300-ply game would crowd out the position on an 8k window, and
#: the position is what the move is decided on. Repetition itself is stated per move, so nothing a
#: rule depends on is lost by the cut.
RECENT_PLIES = 40

#: The rules that decide a game, stated where the model reads them (invariant 12). Both claimable
#: draws are stated because either side may claim one — this seat is asked whenever it can, and its
#: opponent may do it to this seat — and the automatic endings apply to everyone.
RULES = (
    "Checkmating the opponent's king wins the game, and a player who resigns loses it. The game is "
    "drawn by stalemate, by insufficient material, by agreement, when the same position occurs "
    "five times, or after 75 moves by each side with no capture and no pawn move. Either player "
    "may also claim a draw once a position has occurred three times, or after 50 moves by each "
    "side with no capture and no pawn move."
)

#: Why a draw is claimable right now, in the words the state carries. The referee decides which
#: applies (`Referee.claim_draw`); this only says so.
THREEFOLD = "threefold"
FIFTY_MOVES = "fifty_moves"
_CLAIMABLE = {
    THREEFOLD: "This position has now occurred three times, so you may claim a draw.",
    FIFTY_MOVES: (
        "Fifty moves by each side have passed with no capture and no pawn move, so you may claim "
        "a draw."
    ),
}


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """The request body, less the model and routing, and the moves it offers by key."""

    state: dict[str, Any]
    questions: dict[str, Any]
    #: Criterion key → UCI. The key is plain SAN, which is what the model reads; the UCI is what the
    #: referee plays, so an answer is resolved without parsing anything the model returned.
    moves: dict[str, str]

    def body(self, *, model: str) -> dict[str, Any]:
        return {"model": model, "state": self.state, "questions": self.questions}


def _describe(move: MoveFacts) -> str:
    """One legal move, as a sentence a careful person would read the same way twice."""
    if move.castles:
        parts = [f"castle {move.castles}, the king to {move.to_square}"]
    else:
        parts = [f"your {move.piece} from {move.from_square} to {move.to_square}"]
    if move.captures:
        parts.append(f"captures a {move.captures}")
    if move.promotes_to:
        parts.append(f"promotes to a {move.promotes_to}")
    if move.repeats_position:
        parts.append("repeats a position from earlier in this game")
    return "; ".join(parts) + "."


def _recent(board: chess.Board) -> list[str]:
    """The last `RECENT_PLIES` plies as numbered SAN, replayed from the game's own stack."""
    replay = board.root()
    plies: list[tuple[int, bool, str]] = []
    for move in board.move_stack:
        plies.append((replay.fullmove_number, replay.turn == chess.WHITE, replay.san(move)))
        replay.push(move)

    # Numbered the way a score sheet is: `12.Nf3`, then `Nc6`. A Black move that opens the list —
    # because the cut fell there, or the game began with Black to move — is written `12...Nc6`,
    # so it cannot be read as White's.
    tail = plies[-RECENT_PLIES:]
    return [
        f"{number}.{san}" if white else (f"{number}...{san}" if index == 0 else san)
        for index, (number, white, san) in enumerate(tail)
    ]


#: The one test every draw question applies, stated once so offering, accepting and claiming cannot
#: drift apart. **About winning chances, not balance.** The first wording asked whether a draw was
#: "a fair result" from a position "balanced or worse", which is honestly *yes* in any level
#: position — and in the first real game both models sat between 0.3 and 0.5 on it from move one,
#: and the game was agreed drawn at move seven in an open, level middlegame (ADR-0049).
_DRAW_TEST = (
    "Take a draw only when you no longer expect to win by playing on: you stand clearly worse, or "
    "the position is a dead draw with no winning chances left for either side. A level position "
    "that still has play in it is not a reason to draw — either side can still win it."
)
_TAKE_DRAW = "Yes: you stand clearly worse, or neither side has any real winning chances left."
_PLAY_ON = "No: the position still has play in it, or you stand better."


def _last_capture(board: chess.Board, last: chess.Move) -> tuple[str, str] | None:
    """What the opponent's last move took, and on which square, or `None` if it took nothing."""
    board.pop()
    try:
        if board.is_en_passant(last):
            taken: chess.PieceType | None = chess.PAWN
        else:
            victim = board.piece_at(last.to_square)
            taken = victim.piece_type if victim is not None else None
    finally:
        board.push(last)
    if taken is None:
        return None
    return PIECE_NAMES[taken], chess.square_name(last.to_square)


def _material_before(board: chess.Board, last: chess.Move, you: str) -> tuple[int, int]:
    """Material as it stood before the opponent's last move, from the side to move's view."""
    board.pop()
    try:
        before = position_facts(board)
    finally:
        board.push(last)
    white, black = before.white_material, before.black_material
    return (white, black) if you == "white" else (black, white)


def _standing(own: int, theirs: int) -> str:
    """Who is ahead on the conventional piece values, in a word — arithmetic, not a verdict."""
    if own > theirs:
        return "you are ahead"
    if own < theirs:
        return "you are behind"
    return "level"


def _noul(instructions: str, yes: str, no: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


def build_request(
    board: chess.Board,
    *,
    draw_offered: bool = False,
    draw_claimable: str | None = None,
    may_offer_draw: bool = True,
) -> DecisionRequest:
    """The whole request for the side to move in `board`.

    `draw_offered` says the opponent's offer is open; `draw_claimable` names the rule under which a
    draw may be claimed now (`THREEFOLD` or `FIFTY_MOVES`), or `None`. Both are the caller's to
    decide, because the referee and the event log own those facts and this module owns only how
    they are put. `may_offer_draw` is false while this seat's last offer still stands declined —
    see `DecisionTurnRunner._may_offer`.

    `board` must carry its history — the referee's does — because repetition is read from it.
    Nothing here reads the clock, a counter or anything else that changes between two identical
    positions, so the same position with the same history always produces the same bytes.
    """
    facts = position_facts(board)
    you = facts.side_to_move
    opponent = "black" if you == "white" else "white"
    own_material = facts.white_material if you == "white" else facts.black_material
    their_material = facts.black_material if you == "white" else facts.white_material

    moves = legal_move_facts(board)
    last = board.peek() if board.move_stack else None

    state: dict[str, Any] = {
        "you_are": you,
        "position": {
            "fen": board.fen(),
            "you_are_in_check": facts.in_check,
            "white_pieces": list(facts.white_pieces),
            "black_pieces": list(facts.black_pieces),
            # Named buckets beside the numbers: a decision model compares words far better than it
            # compares magnitudes (ADR-0049).
            "material": {
                "yours": own_material,
                "your_opponents": their_material,
                "standing": _standing(own_material, their_material),
            },
        },
        "recent_moves": _recent(board),
    }
    if last is not None:
        board.pop()
        try:
            state["opponent_last_move"] = board.san(last)
        finally:
            board.push(last)
        # **What happened, never what to do about it.** Counted at one instant, material read a
        # queen trade as "nine points behind" for the ply between the two captures, and in the
        # first long real game that one ply told Kev it was losing and it began offering draws.
        # The answer is more history, not a hint: what the last move took, and how material
        # stood before it. Whether and how to take back is the model's to judge — every move
        # that lands on that square already says what it captures, beside every other move, and
        # the best reply to a capture is not always the recapture (ADR-0049).
        captured = _last_capture(board, last)
        if captured is not None:
            piece, square = captured
            state["opponent_last_move_captured"] = f"your {piece} on {square}"
        before = _material_before(board, last, you)
        if before != (own_material, their_material):
            state["position"]["material"]["before_opponents_last_move"] = _standing(*before)

    questions: dict[str, Any] = {
        MOVE_QUESTION: {
            "type": "choice",
            "instructions": (
                f"You are playing chess as {you} against {opponent}, and it is your move. Which of "
                f"your legal moves in `position` is the strongest one to play? {RULES}"
            ),
            "criteria": {move.san: _describe(move) for move in moves},
        }
    }
    # **Everything else a player may do on a turn, one `noul` each** — independent conditions, so
    # each is its own question rather than options bundled into one choice, and code decides what
    # to do when more than one says yes (`DecisionTurnRunner`).
    if draw_claimable is not None:
        state["draw_claim"] = _CLAIMABLE[draw_claimable]
        questions[CLAIM_DRAW_QUESTION] = _noul(
            f"You are playing chess as {you}, and you may claim a draw now (`draw_claim`). "
            f"{_DRAW_TEST} Should you claim the draw?",
            _TAKE_DRAW,
            _PLAY_ON,
        )
    if draw_offered:
        state["draw_offer"] = f"{opponent.capitalize()} has offered a draw."
        questions[ACCEPT_DRAW_QUESTION] = _noul(
            f"You are playing chess as {you}, and your opponent has offered a draw "
            f"(`draw_offer`). {_DRAW_TEST} Should you accept it?",
            _TAKE_DRAW,
            _PLAY_ON,
        )
    elif may_offer_draw:
        # Not asked while the opponent's own offer is open: the answer to that is accepting it.
        questions[OFFER_DRAW_QUESTION] = _noul(
            f"You are playing chess as {you}. {_DRAW_TEST} Should you offer your opponent a draw "
            "now, while still playing your move?",
            _TAKE_DRAW,
            _PLAY_ON,
        )
    questions[RESIGN_QUESTION] = _noul(
        f"You are playing chess as {you}. Is the game in `position` lost for you beyond any doubt, "
        "so that you should resign? Resign only when nothing can save it: a decisive material "
        "deficit with no counterplay, or a mate that cannot be stopped. Standing worse, or being "
        "a pawn or two down, is never a reason to resign.",
        "Resign: the game is lost beyond any doubt.",
        "Play on: the game can still be saved, drawn or won.",
    )

    return DecisionRequest(
        state=state,
        questions=questions,
        moves={move.san: move.uci for move in moves},
    )


__all__ = [
    "ACCEPT_DRAW_QUESTION",
    "CLAIM_DRAW_QUESTION",
    "DECISION_VERSION",
    "FIFTY_MOVES",
    "MOVE_QUESTION",
    "OFFER_DRAW_QUESTION",
    "RECENT_PLIES",
    "RESIGN_QUESTION",
    "THREEFOLD",
    "DecisionRequest",
    "build_request",
]

"""What a decision model is shown, and what it is asked (ADR-0049).

The decision harness's equivalent of `prompts.py` and `tools.py` together, and versioned the same
way for the same reason: results produced under different questions are not comparable (BENCH-04).
**Changing a word of the output — a fact added, a field renamed, a criterion reworded — is a bump
of `DECISION_VERSION`**, and `tests/agents/test_decision_request.py` holds a recorded request that
fails until the bump is made.

It has a version of its own, apart from `PROMPT_VERSION` and `TOOL_SCHEMA_VERSION`, because the two
harnesses change for different reasons and must be able to do so without retiring each other's
games: a new rule stated to the chat models is not a change to what a decision model is asked.

**Everything a chat seat can do on a turn, a decision seat is asked about**: its move, and what it
does with the turn — play on, offer a draw with the move, resign, accept an open offer or claim an
open draw. The chat seat does these through tools; this seat ranks them in one `choice`, and the
option it ranks first is what happens (`DecisionTurnRunner`).

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
#:
#: `d2` on 2026-09-27: the four `noul`s become one `action` choice (ADR-0051). A `noul` needs a
#: threshold, and a threshold does not carry between models — the same dead-drawn ending read 0.29
#: to Jev and 0.53 to Kev — so one gate for every model decided some games by how well a model's
#: scale lined up with our number. A choice is relative: the option a model ranks first happens, on
#: its own scale, with nothing to tune per model.
DECISION_VERSION = "d2"

#: The question keys. Ours, not the model's — the API never sends a key to the model, which is why
#: every instruction below carries its full meaning on its own.
MOVE_QUESTION = "move"
ACTION_QUESTION = "action"

#: The options of the action question. `PLAY_ON` is always first, so an equal ranking — which a
#: `choice` breaks by order — falls to the ordinary turn rather than to ending the game.
PLAY_ON = "play_on"
OFFER_DRAW = "offer_draw"
RESIGN = "resign"
ACCEPT_DRAW = "accept_draw"
CLAIM_DRAW = "claim_draw"

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
    #: The actions offered this turn, in the order the model was shown them.
    actions: tuple[str, ...] = ()

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


#: How a draw and a resignation are judged, stated once in the action question's instructions.
#: **About winning chances, not balance.** The first wording asked whether a draw was "a fair
#: result" from a position "balanced or worse", which is honestly *yes* in any level position, and
#: the first real game was agreed drawn at move seven in an open, level middlegame (ADR-0049).
_ACTION_GUIDE = (
    "Take a draw only when you no longer expect to win by playing on: you stand clearly worse, or "
    "the position is a dead draw with no winning chances left for either side. A level position "
    "that still has play in it is not a reason to draw. Resign only when the game is lost beyond "
    "any doubt: a decisive material deficit with no counterplay, or a mate that cannot be "
    "stopped. Standing worse, or being a pawn or two down, is never a reason to resign."
)


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
    see `DecisionTurnRunner._may_offer`. Together they decide which actions are offered.

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
    # **What it does with the turn, as one choice among the actions open to it** (ADR-0051). The
    # actions are mutually exclusive — a player does exactly one — which is what a `choice` is for,
    # and a choice is relative, so the model's own ranking decides with no threshold of ours.
    actions: dict[str, str] = {PLAY_ON: "Play your move and carry on with the game."}
    if draw_claimable is not None:
        state["draw_claim"] = _CLAIMABLE[draw_claimable]
        actions[CLAIM_DRAW] = (
            "Claim the draw (`draw_claim`) instead of moving; the game ends drawn."
        )
    if draw_offered:
        state["draw_offer"] = f"{opponent.capitalize()} has offered a draw."
        actions[ACCEPT_DRAW] = (
            "Accept your opponent's draw offer (`draw_offer`); the game ends drawn."
        )
    elif may_offer_draw:
        # Not offered while the opponent's own offer is open: the answer to that is accepting it.
        actions[OFFER_DRAW] = (
            "Play your move and offer your opponent a draw; they may accept it or play on."
        )
    actions[RESIGN] = "Resign instead of moving; the game ends and you lose it."
    questions[ACTION_QUESTION] = {
        "type": "choice",
        "instructions": (
            f"You are playing chess as {you} against {opponent}, and it is your turn. What will you "
            f"do with it? {_ACTION_GUIDE}"
        ),
        "criteria": actions,
    }

    return DecisionRequest(
        state=state,
        questions=questions,
        moves={move.san: move.uci for move in moves},
        actions=tuple(actions),
    )


__all__ = [
    "ACCEPT_DRAW",
    "ACTION_QUESTION",
    "CLAIM_DRAW",
    "DECISION_VERSION",
    "FIFTY_MOVES",
    "MOVE_QUESTION",
    "OFFER_DRAW",
    "PLAY_ON",
    "RECENT_PLIES",
    "RESIGN",
    "THREEFOLD",
    "DecisionRequest",
    "build_request",
]

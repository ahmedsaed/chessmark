# 0040. What a board shows, and what the prompt owes you

**Status:** Accepted
**Date:** 2026-09-13
**Amends:** [0020](0020-claimable-draws.md) — its disclosure rule, applied to the forfeit it missed,
and its draw tooling, which turns out never to have worked between two models. Follows
[0037](0037-a-turn-ends-when-the-model-stops.md), whose harness change the prompt never caught up
with. Bumps `PROMPT_VERSION` to **v3** and `TOOL_SCHEMA_VERSION` to **v3**.

## Context

Five findings, from reading the prompt and the tool surface against the games they produced. Three
of them are the same mistake in different places: **the harness knows a rule, and the model does
not.**

### `get_legal_moves` was solving the position

```python
**({"check": True} if move.is_check else {}),
**({"checkmate": True} if move.is_checkmate else {}),
```

Every legal move, every turn, flagged for whether it gives check and whether it is mate. That is a
one-ply search with terminal evaluation, run by us and handed over free. **No model playing
Chessmark has ever had to find mate in one.** It was told.

The defence offered was that every board client highlights legal moves and captures, so this is the
same kind of aid. It is not, and the line falls precisely between the fields: lichess and
chess.com mark legal destination squares and mark an occupied one differently, and **neither of
them tells you which move is mate.**

Three consequences, in rising order of seriousness:

* Missing mate in one is the most common way a weak model throws away a won position, so flagging
  it deletes the difference between a strong model and a weak one at exactly the moment a game is
  decided.
* It rewards *calling the tool* rather than playing chess. A seat tracking the board itself gets
  nothing; a seat calling `get_legal_moves` every turn gets a tactical oracle — and the prompt
  pushes toward calling it.
* `check: True` makes the ADR-0020 shuffle — chasing a bare king with checks into a repetition —
  findable without seeing anything at all. The game that produced ADR-0020 was drawn exactly that
  way.

### A forfeit nobody was told about

`MAX_NUDGES` is 3, so a **fourth** reply with no tool call ends the game as `error_forfeit`. The
prompt stated the illegal-move forfeit in full and said nothing whatever about this one.

`1815a53f` ended `0-1` at ply 176 on it, rated, against a model that had played 131 competent plies
first. That is ADR-0020's finding word for word — *"nobody had told the model the rule existed"* —
recurring one paragraph above where ADR-0020 fixed it.

The two nudges disagreed about this among themselves. The repeated-call nudge names the stake:

> You have {left} tool rounds left before you forfeit this game — call `make_move` now.

`NUDGE_PROMPT`, sitting in front of the forfeit that actually ended a game, did not.

### `offer_draw` did nothing

```python
def _offer_draw(self, _arguments):
    # The opponent answers on its own turn; nothing changes yet.
    return ToolResult(payload={"ok": True, "offered": True, "detail": "Draw offered. ..."})
```

No event, no message, nothing. It told the *offering* seat its offer had been delivered and the
opponent was never informed. The only code that recorded an offer was `orchestration/human.py`.

There was no `accept_draw` either, which `DRAW_OFFER_RECEIVED` admitted outright: *"There is no
tool to accept it, so play on."* So `Termination.AGREED_DRAW` — a rated termination, listed in the
enum, handled by the referee — **was unreachable in every model-vs-model game ever played**, while
the tool that suggests otherwise sat in the cached prefix of every request.

Underneath it, `open_draw_offer` was keyed on the position: an offer is open only if
`payload["ply"] == referee.ply`. True for a human, who offers as a separate action and does not
move afterwards. Wrong for a model, which **must still move in the turn it offers in** — so a model
offer was cancelled one ply before the opponent could ever see it, even had it been delivered.

### The prompt still described the old turn loop

> Every turn, you must end by calling `make_move` exactly once.

ADR-0037 made a turn end when the model *stops*, not when it moves. Models read "end by calling
`make_move`" as *keep going until the turn is over, then move again*. In `9450f060`, every single
turn of the black seat:

```
 6 assistant  make_move   →   7 tool  {"fen": ...}          move played
 8 assistant  get_board   →   9 tool  {"board": ...}
10 assistant  make_move   →  11 tool  {"error": "already_moved"}
12 assistant  make_move   →      (no result — the turn ended here)
```

Wasted calls in every game, and before ADR-0039 it was also what left dangling tool-call rows.

### The ranked prompt named a tool that was not there

> This is a ranked game. Do not use the `say` tool; it is disabled.

`say` is already removed from the schema in a ranked game. Naming it introduces a tool the model
cannot see, which is how an invented tool call gets produced — the exact trap
`DRAW_OFFER_RECEIVED` was written to avoid.

## Decision

**`get_legal_moves` returns what a board shows and nothing a board does not.** `check` and
`checkmate` are removed; `capture` and `promotion` stay, because an occupied destination square is
one glance at the position and the promotion piece is a mechanical choice the board asks you for.
The move itself is still listed — this declines to analyse a position, it does not hide a legal
move, and ADR-0002 still returns the full list on every illegal one.

**The silence forfeit is disclosed**, in *Rules that will be enforced*, beside the illegal-move
forfeit, with its threshold interpolated from `MAX_NUDGES` so the prompt cannot drift from the code
enforcing it. `NUDGE_PROMPT` counts down like its sibling.

**A draw by agreement works.** `offer_draw` returns `offers_draw`, which the turn loop turns into a
`draw_offered` event — one row, as every state change gets (invariant 7). A new `accept_draw` ends
the game as a draw, and a refused acceptance is **not an illegal move**, for ADR-0020's reason: a
model asking whether an offer is open has broken no rule.

The offer reaches the opponent **in the turn prompt**, which is already the one message saying what
has happened since that seat last acted; it keeps the offer next to the move it arrived with, and
keeps the transcript one append per turn.

**An offer lapses when the player it was made to moves** — that move is the decline, as it is over
a board. The offerer's own move does not lapse it. `open_draw_offer` compares event order instead
of position, which is right for both the human and the model path, and it moves to
`db/repositories.py` so both read one function rather than two copies.

**The prompt describes the turn loop it actually has**, and names no tool the model cannot see.

## Alternatives considered

**Keep `checkmate`, drop only `check`.** Halfway, and the wrong half: mate is the flag that
actually decides games.

**Deliver the draw offer as its own transcript message**, as the human path does with
`_tell_opponent`. It works, and it puts two adjacent user messages where one will do — the turn
prompt exists to say what has happened, and an offer is that.

**Disclose the silence forfeit without a countdown in the nudge.** The disclosure alone satisfies
invariant 12, but a model three replies into a forfeit has no way to know it, and the other nudge
already sets the precedent.

**Leave `offer_draw` as decoration, or delete it.** Deleting was tempting — it cost schema tokens
in every cached prefix and did nothing. But agreeing a draw is a real chess resource, the referee
already implements it, and a benchmark that cannot reach one of its own rated terminations is
measuring less than it claims.

## Consequences

**The board clears, and this is why it is one bump rather than three.** Three of these are
independently major — removing information, disclosing a game-deciding rule, and adding a tool — so
no combination of them preserves the v2 standings. Landing them together spends that once. The 75
v2 games are not deleted; they keep their version and `bench.ratable.same_task` declines to mix
them, exactly as ADR-0020 did to v1.

What clears with them is a board on which `checkmate: True` fed every seat, one game forfeited on
an undisclosed rule, and two forfeited for a host's parser (ADR-0015). It preserved a
mismeasurement rather than a measurement.

**Rebuilding takes about a month** at the free tier's current rate — roughly two to three rated
games a day. That is the real cost of this ADR, and it is worth saying plainly: the games were
never the expensive part, the calendar is.

**Expect longer games and more `PLY_CAP`.** Small models that could be handed mate now have to find
it. That is the measurement working, and it will not look like it at first.

**`TOOL_SCHEMA_VERSION` is bumped but still unchecked.** `bench.ratable.GameFacts` does not carry
it, so `judge` cannot exclude a game for the tool surface it played under — only for its prompt
version. Here that is harmless, because `PROMPT_VERSION` moves with it. It will not stay harmless:
a tool-output change on its own would alter the task while both versions sat still. Recorded in
ROADMAP's *Known gaps* rather than fixed here, because closing it properly means deciding what a
minor tool change is, which is ADR-0038's question asked again about a different artefact.

**A game in progress across the deploy keeps its stored system prompt and sees the new tool list**,
as at ADR-0020: it gains `accept_draw` and loses two flags mid-game. One cache miss, no
contradiction — and its stored prompt still describes a `get_legal_moves` slightly more generous
than the one it now gets, which is a difference in our favour rather than the model's.

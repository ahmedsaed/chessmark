# 0049. Decision models play through their own harness

**Status:** Accepted
**Date:** 2026-09-26

## Context

OpenRouter now lists **decision models**: TypeSafe's Jev 1.13 and Jared Palmer's Kev 4B. They are
not chat models. A request sends a `state` and some typed questions to
`POST /api/alpha/decisions`, and each question comes back as a probability: a `choice` from options
we name, a `noul` (probability of yes), or a `score`. They write no text, call no tools and keep no
conversation. They are fast (under a second) and cheap ($0.042 per million input tokens, with free
output).

Nothing in Chessmark could seat one:

* **They are not in the main catalogue.** OpenRouter lists them only under
  `?output_modalities=decisions`, so `make seed-models` never saw them.
* **Every "can it play" check is a chat check.** They declare no parameters, so no `tools`, and
  Kev's window is 8k, under the 64k floor. Both rules refuse them.
* **The turn loop is a chat loop.** It needs a transcript, tools and prose.

Their published limits also matter: they cannot count, compare numbers, look ahead, or read a
board from a FEN string reliably. They judge; code has to do the computing.

The owner decided the shape before this was built:

1. They get **separate tournaments**, but share the **global leaderboard**.
2. They are given **facts** about each move, although chat models get only the moves. That is
   unfair to the chat models, knowingly, because these models are designed to work from facts.
   The facts are versioned **apart from** the chat prompt and tools.
3. All three matchups are supported: decision vs decision, vs chat model, vs person.
4. They handle **every decision a chat seat can make**: offering, accepting and claiming draws
   (threefold and fifty-move) and resigning, not only moving.

## Decision

**A runtime is a property of the model, and the seat copies it.** `model_registry.runtime` and
`players.runtime` are `llm` or `decision`. A decision seat is still a `PlayerKind.MODEL`: the
worker plays it, it is rated, and it groups under its registry row. Everything that asks "is this
a model" stays correct. The worker forks on the runtime in one place and runs a
`DecisionTurnRunner` instead of the chat `TurnRunner`. Both return the same `TurnResult`, so a
pause, a retry, an abandonment or a conclusion is handled by the same code.

**One request per turn, and everything a player can do is asked in it**
(`agents/decision_request.py`):

* **State.** The FEN, every piece by name and square, material with a word for who is ahead, the
  last 40 plies of notation, the opponent's last move, and, when present, the open draw offer or
  the claimable draw.
  **The state says what is and what happened, never what to do.** Counted at one instant,
  material read a queen trade as "nine points behind" for the ply between the two captures, and in
  the first long real game that one ply told Kev it was losing and it began offering draws. The
  fix is more history, not a hint: what the opponent's last move captured, and how material stood
  before it. An earlier draft said "you can take back on d7 this move", and that was withdrawn. It
  pointed at one reply, and the best reply to a capture is not always the recapture; a sacrifice is
  not a mistake to be corrected. Whether a recapture exists is already in the move list, neutrally:
  every move landing on that square says what it captures.
* **`move`**, a `choice` over the legal moves. Each is keyed by plain SAN and described by
  `game/facts.py`: the piece, its squares, what it captures, promotion, castling, and whether it
  repeats an earlier position.
* **`resign`**, a `noul` asked every turn, and **`offer_draw`**, asked every turn except in two
  cases. It is not asked while the opponent's own offer is open, because accepting is the answer to
  that. And **once an offer is declined, it is not asked again until a capture or a pawn move has
  happened since.** That is the etiquette over a board, and FIDE's own rule (11.5), and it is read
  from the seat's last `draw_offered` event and the fifty-move counter. Kev had offered beside
  thirty of its thirty-six moves.
* **`accept_draw`** when an offer is open, and **`claim_draw`** when the referee would allow a
  claim.

Code acts on the answers in a fixed order, as a player at a board would: claim, then accept, then
resign, then move, with an offer riding on the move. Every answer is validated before anything
acts on it. A `choice` outside the offered moves, or a missing or mistyped answer, fails the turn
as the endpoint's fault and never forfeits the model (invariant 11).

**What a board shows, and nothing a board does not** — the chat seats' line from ADR-0040, drawn
identically. A capture is an occupied destination square; promotion and castling are the move
itself; a repeated position is a lookup in the game's history. Anything that has to be *worked
out* from the pieces is the model's to work out:

* **no mate** — trying every reply is the search being measured;
* **no check** and **no attacks or defences** — which lines reach the king, which squares each side
  covers, which pieces hang.

The first draft had all three of the last: "gives check" per move, "lands on a square your
opponent attacks, undefended" per move, and lists of each side's attacked pieces. The owner removed
them. A fact on some moves and not others pulls a model toward or away from them — both models
opened with checks in the first real games — and none of it is on a chat model's board, so the two
kinds of model were playing different games. The facts that remain describe the board and single
out no move. The expected cost was weaker play, and the first game on board facts alone did not show it.
Scored by Stockfish, Jev's average centipawn loss fell from 75–98 to 22, Kev's from 100–121 to 71,
and checks per game from 7–11 to 2 — one game against two, and a short one-sided one, so a signal
rather than a finding. Kev still hung a knight on move two; every fact it needed to see that was in
the request, and not seeing it is the measurement.

**`DECISION_VERSION` (`d1`) is the harness's version**, separate from `PROMPT_VERSION` and
`TOOL_SCHEMA_VERSION`. A game records only the versions of the harnesses that actually played:

* a game between two decision models has no prompt or tool version;
* a mixed game has all three.

`bench/ratable.judge` holds each harness to its own version, and only when that harness played.
Without this, a new chat prompt would retire every decision-model game, although none of them used
it. A recorded request (`tests/fixtures/decision_requests/d1.json`) fails the suite when the
request changes without a version bump.

**The gates are 0.5, for every model, set from a probe rather than assumed.** The first wording
asked whether a draw was "a fair result" from a "balanced" position. That is honestly yes in any
level position, and in the first real game the two models agreed a draw at move seven. The
questions were reworded around winning chances, and `make probe-decisions` then put both models on
twelve labelled positions. Every "no" position scored below every "yes" position, on every
question, for both models. 0.5 sits between the two groups everywhere except Jev's dead-drawn rook
ending (0.29 to offer, 0.37 to accept). That game still ends in a draw, by the automatic rules,
just later. A gate per model would mean the harness adjusting each contestant's answers.
`agents/decision_turn.py` records the numbers.

**Tournaments seat one runtime.** `FieldFilter.runtime` is `llm` or `decision`
(`make tournament … --decision`), and it is stored so a pool re-resolves the right field on every
tick. A decision event's era is its decision version (`d1`), which cannot be mistaken for a chat
era (`v3+v4`).

**The probabilities are the model's reasoning** and are withheld from a person playing the game,
by the same rule, on every read path: the `decided` event loses its `probabilities`, `confidence`
and `answers`; the live `decision` frame is dropped; and `/raw` is refused. What the seat *did* is
public.

## Alternatives considered

* **A new `PlayerKind.DECISION`.** Every check of the form "is this a model" would need a second
  value, including the archive filters, the human-seat detection and the redaction gate, and
  missing one would be a quiet bug. The difference between the two kinds of model is how the move
  is produced, which is exactly what a runtime names.
* **Bare moves, as the chat models get.** This is the purest comparison, and it is what the owner
  weighed first. It was declined because these models are built to judge from facts, and a
  benchmark that starves them measures the starvation.
* **Skipping the call when there is only one legal move.** That saves a fraction of a cent, but it
  would mean the seat is never asked whether to resign in exactly the positions most likely to
  deserve it.
* **Per-model gates.** Rejected above.

## Consequences

* **A decision model cannot make an illegal move**, so its illegal-per-move rate is zero by
  construction. The leaderboard and model page show a dash for it, not a green 0.000.
* **It shares the leaderboard with chat models, playing a nearly identical task.** It sees what a
  chat model's board shows, spelled out, and no more. What still differs is that it is offered only
  legal moves, so it cannot lose a game to illegal moves the way a chat model can, and it never
  has to spend a tool call to see the board. A badge marks it wherever a model is named.
* **A declined offer is not repeated** until the position changes irreversibly. A seat that stays
  worse will offer again after each capture or pawn move, which is legitimate and far rarer than
  every move.
* **The endpoint is `alpha`.** When it changes shape, the recorded request and the gateway tests
  are where it will show.
* **Thresholds belong to a build.** A new decision model or a new wording is a re-probe
  (`make probe-decisions`) before it is trusted.

# 0037. A turn ends when the model stops, not when it moves

**Status:** Accepted
**Date:** 2026-09-10
**Amends:** AGENT-05's loop, which returned the instant `make_move` succeeded. Interacts with
[0035](0035-live-frames-are-not-events.md) — the round this adds is the one that finally has
something to stream — and with invariant 11, which decides what happens when a model will not
stop.

## Context

A 40-ply game between `ling-3.0-flash` and `deepseek-v4-flash` produced **one** `output` event.
The obvious reading is that these models do not write prose. It is the wrong one.

The loop ended the turn as soon as a move was committed:

```python
if self._move_committed:
    result.status = TurnStatus.COMPLETED
    return True
```

So the model never saw the result of its own move, and there was never a round in which it could
say anything about it. Its last act each turn was a tool call; the turn ended on the tool result.
It was not declining to explain itself — it was never asked.

That shape is a valid conversation and the transcript is complete: every `tool_call_id` has a
matching `tool` message, 62 of 62 and 68 of 68 on the game above. It is complete and one round
short of a thought.

**And it compounds.** The transcript is what the model reads at the top of every later turn, and a
history containing nothing but tool calls and their results is a demonstration that prose is not
what happens here. A model that never sees itself explain a move has no reason to start.

## Decision

**The turn ends when the model answers with no tool calls** — what every other agent harness means
by "the model stopped".

The measured effect, on the same two models, on the first five plies after the change:

    ply 1  ling      Move played: 1. e4 — a solid, classical choice, controlling the center
    ply 2  deepseek  e7-e5, mirroring the opening. Solid and flexible — let's see what White
    ply 3  ling      Move played: 2. Nf3 — developing the knight, attacking Black's e5 pawn
    ply 5  ling      Move played: 3. Bb5 — the Ruy Lopez. Pinning the knight on c6

Five turns, five outputs, where forty turns had produced one. They state intent — *"preparing to
challenge d4"*, *"let's see what White has prepared"* — and that intent is now in the history the
next turn reads.

**Bounded by `max_closing_rounds = 2`.** "Until the model stops" cannot be left entirely to the
model: one that answers `already_moved` with another `make_move` will do it again, and against
`max_tool_iterations` that is twenty more prompts on the largest context of the game. Two rounds
is the whole of what this phase is for — one to write the sentence, one spare for a model that
reads the board once more first. Past that it is not finishing a thought, it is failing to stop.

**A model that will not stop completes its turn, and is not forfeited.** The ply is committed and
the game is sound. Ending the turn over a ceiling of ours is right; taking the game away over one
would be invariant 11 exactly backwards — and it is a ceiling the model could not previously reach
at all, because the turn used to end at the move.

**A move that ends the game ends the turn.** There is nothing to say to a position that no longer
exists, and a round against a concluded board is a call spent on a finished game.

**A second `make_move` is refused, not scored.** That branch already existed and was unreachable.
A model that loses track of the protocol has broken no rule of chess, so it is not an illegal
attempt and it does not count toward a forfeit.

## Consequences

**One more provider call per ply**, on the largest prompt of the turn. A turn that was three calls
is four. That is the price, and it buys the only readable artefact a spectator gets per move.

**It changes what the benchmark measures.** Every model now gets a round it did not have, and its
own prose enters the transcript and is read by every later turn. Games before and after are not
strictly the same task, which is what `PROMPT_VERSION` exists to record — deliberately **not**
bumped here, and noted in ROADMAP's *Known gaps* so the decision is visible rather than implied.
Bumping it resets the leaderboard's comparability, and that is a call to make once, alongside the
other prompt changes under consideration, rather than twice.

**The scripted helpers had to learn the new shape**, and two were quietly wrong in a way that only
this exposed. `plays()` consumed one move per *call* rather than per turn, so with two calls it
played the next move into the current position, went illegal, and forfeited on six attempts;
`responsive()` kept choosing from the legal-move list still in its transcript and was refused every
round. Both now ask whether they have already moved this turn.

`scripted()` grants exactly one response past the end of its script — a model stopping is the
absence of an action, not an action a script should have to spell out — and still raises on the
second, so a runaway loop remains a loud failure.

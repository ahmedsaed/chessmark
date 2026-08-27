# 0018. The model summarises its own history when the window fills

**Status:** Accepted
**Date:** 2026-08-27
**Amends:** [0003](0003-full-transcript-prompt-caching.md) — the append-only, byte-stable message
list now has one named exception. Lowers the AGENT-14 context floor from 128k to 64k.

## Context

The transcript is replayed whole on every turn (ADR-0003) and grows about **1,818 tokens per ply**,
measured across real games. So a turn's prompt *is* the context it needs, and the arithmetic is
unforgiving: 128k covers roughly seventy plies of a possible three hundred, and a talkative model
gets there sooner. Free models observed at 1,100–1,950 output tokens per call reach it faster still.

Running out is not a polite failure. `context_exceeded` is in `FORFEIT_TERMINATIONS`, so a model
that fills its window records a **loss** — a claim on the leaderboard that it played badly, when
what actually happened is that our harness ran out of room.

The floor was the only defence, and it does not work. Raising it moves the wall without removing it;
no threshold makes a 300-ply game safe, since that would need ~545k tokens and would exclude almost
the entire catalogue. A floor buys plies and then forfeits.

**And chess makes the alternative unusually safe.** The server is the only authority on board state
(invariant 1) and the model can call `get_board_state` and `get_legal_moves` at any time. A summary
that loses detail cannot corrupt the game, because nothing the model believes about the position is
load-bearing. That is not true of a general agent whose context *is* its world, and it is why
compaction is a better fit here than almost anywhere else.

## Decision

**At a threshold, the model summarises its own earlier turns and plays on from the summary.**

* **The trigger is measured, not estimated.** It fires when
  `endpoint_context - prompt_tokens < max(reserve, 10% of context)`, using the provider's own
  `usage.prompt_tokens` from the previous round-trip (invariant 4). "Within 20k of the limit" and
  "past 90%" are one idea from opposite ends — a completion reserve and a percentage — and taking
  the larger scales from a 64k window to a 1M one with no special case. A character estimate is used
  only before a turn's first response, where nothing exact exists, and it deliberately
  over-estimates: compacting early costs a cache miss, compacting late forfeits.

* **The cut lands on a turn boundary.** The last four *turns* are kept verbatim, not the last four
  messages. A turn is three to five messages and every provider rejects a `tool` result whose
  `tool_calls` parent is missing, so counting messages would cut mid-turn and 400.

* **The model summarises itself**, on its own pinned endpoint, with tools withheld and a small
  completion cap. The cap matters: sending the near-full history with the usual 64,000-token
  `max_tokens` would be refused for exactly the reason compaction exists. A cheaper third model
  would be cheaper and would put another model's prose into a benchmark record.

* **Nothing is deleted.** Folded rows keep their place in `transcript_messages` and gain a
  `superseded_at`; the builder stops sending them. The record stays verbatim (invariant 3) and only
  the request shrinks. One live summary at a time, because a previous summary is folded into the
  next one.

* **The model is told.** The summary is replayed as something *given* to it, with a pointer back to
  `get_board_state`. A model that mistakes a paraphrase for its own recollection acts on it; one
  that knows its history was folded re-reads the board, which is authoritative.

* **Compacted games stay ranked.** Context management is part of being a long-horizon agent, and a
  benchmark that excluded it would be measuring models on a harness nobody would build. The count
  is published per seat instead — `players.compactions`, on the game page beside the illegal
  attempts, because a seat that compacted four times filled its window four times.

* **The floor drops to 64k**, and its job changes with it: not "can this model finish a game" — no
  window guarantees that — but "can it start one, and is there room for a summary plus the turns
  kept around it".

**`max_completion_tokens` is clamped** to what the endpoint will accept. This was a bug independent
of compaction: a flat 64,000 reconciled against nothing asked a 65,536-token endpoint for 65,810
tokens and was refused, abandoning a game at ply 10. A 64k floor without the clamp would have
recreated that failure for every model in the new band.

## Alternatives considered

**Raise the floor instead.** Tried, at 128k. It buys seventy plies. The wall is still there and the
penalty is still a forfeit.

**Drop the oldest messages without summarising.** Cheaper — no extra call — and it throws away the
one thing worth keeping. Chess history is not uniformly valuable: the opening and the plan matter at
move 60, the exact tool calls of move 3 do not. A summary keeps the first and discards the second.

**Summarise with a cheap fixed model.** Cheaper per compaction, and it writes another model's words
into the transcript a benchmark result rests on. Attribution matters more than the saving.

**Let the model call a `compact` tool when it feels full.** Attractive, and it makes the benchmark
partly a test of self-monitoring rather than of chess. It also fails when a model does not notice,
which is exactly when it is needed. Automatic, with the count published.

## Consequences

**One cache miss per compaction**, since the cacheable prefix is rewritten by definition — the named
exception to invariant 2. That is why compaction cuts *deep* rather than to just under the
threshold: folding to 89% would mean folding again three plies later and paying repeatedly. It is
also once per turn at most, which stops a small window spending a call per round-trip.

**A compacting call can itself fail.** A rate limit pauses the game (ADR-0017) and it is retried
whole. Anything else returns without compacting, the turn proceeds on the un-compacted history, and
the over-large prompt is refused — which fails the turn, rolls it back, and retries, compacting
again. That is the intended path: carrying on regardless would spend a call to be told the prompt is
too large and then abandon the game.

**Two games of the same model may now see different histories.** One that compacted and one that did
not are not identical conditions, and the leaderboard does not separate them. That is deliberate —
see the ranked decision above — but it is a real loss of comparability, and the per-seat count is
what makes it visible rather than silent.

**A `downgrade` past this migration replays folded history.** Nothing is lost, since nothing was
deleted; a long game may simply stop fitting its window until the migration is re-applied.

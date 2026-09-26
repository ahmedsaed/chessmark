# 0053. Every failure keeps its rounds, and a crash spends an attempt

**Status:** Accepted
**Date:** 2026-09-26
**Amends:** [0045](0045-a-turn-keeps-the-rounds-it-completed.md). It kept a turn's completed
rounds only when the next attempt would send the same request. Every classified failure keeps
them now.

## Context

OpenRouter's analytics report cost and request counts per `session_id`, and every game's session is
`game-<id>`. Once those could be read, each game's record could be checked against what OpenRouter
billed for it. Production's 155 game sessions over 31 days:

* **Money**: $0.4622 billed against $0.4144 recorded. Almost all of the gap predates ADR-0045.
  From 16 September the two agree to $0.0001.
* **Answers**: about 350 successful generations since then (3.5%) that no record of ours holds.
  They were free models, so they cost nothing but the daily allowance. On paid models they would be
  money.

They were traced one game at a time, generation id against generation id:

* **A rollback on every other failure.** ADR-0045 kept the rounds for a provider that stopped
  answering and rolled back everything else: a rejected request, no room to answer, broken token
  accounting, our output ceiling, a mangled tool call. Each of those threw away rounds that had been
  answered and billed, and the retry paid for them again. `19e69569` hit our own `max_tokens`
  ceiling and replayed its whole turn five times before it was abandoned.
* **A crash on resume.** A resumed turn numbered its tool calls from a counter that skips unknown
  tools, and collided with its own rows. The worker died, the stall sweep requeued the game 45
  minutes later, and it crashed again. `c2fd378a` and `c4550202` each spent a day that way: 76 and
  47 billed answers. The numbering (#118) and the worker's survival (#119) are fixed on `main`. What
  remained was that a crash cost the game nothing, so a crash caused by the game's own state never
  stopped.

## Decision

**Every classified failure keeps the rounds its attempt completed.** That covers a provider
outage or rate limit, a rejected request, no room to answer, broken token accounting, our output
ceiling, and a mangled tool call. The turn is `INTERRUPTED` and the next attempt continues it.
ADR-0045 assumed a smaller transcript makes a changed request easier. That assumption is not needed:
the worker's rescue for a rejected request already shrinks the transcript outside the turn, and a
compaction done inside the turn now survives the failure too, where before it was rolled back with
the turn. Only an unclassified exception, a bug, still rolls back, because its state cannot be
trusted.

**A failure after the move completes the turn**, whichever failure it is. The rule was written for
provider errors (ADR-0037's closing round). Every path shares it now, because every path keeps its
rounds, and an interrupted turn with a committed move would be resumed for a later ply.

**Our own output ceiling is treated as a rejected request.** The same request is cut off at the
same place, so it gets one rescue that shrinks the transcript, which also lets our clamp ask for
more, and then an end. It no longer gets five identical retries. The endpoint's own ceiling is still
retried: a model often finishes on the next attempt. No room to answer is classified the same way,
since the next request has to be smaller.

**A crash spends one of the job's attempts.** It is requeued at the next attempt at once. After
`MAX_JOB_ATTEMPTS` the game is abandoned, never forfeited, because a bug of ours is not a finding
about a player (invariant 11). It is still recorded for `status` (OPS-21).

## Consequences

* **The record holds what OpenRouter billed**, except for requests that never produced an answer we
  saw: a timeout, a refusal. Per-game reconciliation against OpenRouter covers those.
* **A kept round is never paid for twice**, because the retry continues from it.
* **A rejected request abandons a game with its record intact.** Before, the game ended with none of
  the rounds it had paid for on the record.
* **A deterministic crash ends a game within five attempts**, not after a day of sweeps.

# 0045. A turn keeps the rounds it completed, and the retry continues it

**Status:** Proposed
**Date:** 2026-09-15
**Relates to:** [ADR-0003](0003-full-transcript-prompt-caching.md), [ADR-0007](0007-turn-level-jobs.md), [ADR-0019](0019-harness-bounds-are-not-findings.md), [ADR-0037](0037-a-turn-ends-when-the-model-stops.md)

## Context

A turn runs inside one transaction, and a provider failure raises out of it:

```python
# A provider failure is ours, not the model's (AGENT-09). Raising discards the whole
# turn so the retry starts from an untouched transcript.
if result.status is TurnStatus.FAILED and result.outcome is None:
    raise ProviderFailureError(result)
```

The comment states the reason and the reason is sound: a half-written turn can leave an assistant
message carrying `tool_calls` that nothing answered, and the transcript is append-only, so that
seat is then refused by **every later turn of the game**. No pause, retry or resume clears it. It
corrupted 242 rows across 14 seats before a strict endpoint noticed.

But the rollback throws away far more than the thing that must not survive.

### What is actually lost

A turn refused on its third call has already made two that were answered and billed. The rollback
discards:

* the `Turn` row and its `game_events`;
* the **`llm_calls` rows** — the verbatim payloads of calls that really happened (invariant 3);
* the `tool_calls` rows;
* every transcript round the model completed;
* `record_spend(session, user_id, …)`, the owner's daily ledger.

`GlobalBudget.record` writes to Redis and therefore *survives*, so the global counter and the
per-user ledger disagree by exactly the turns a contended endpoint eats — which is when the figure
is largest.

The retry then re-runs the discarded calls. In `f129b600` the rolled-back turns are visible as the
gaps in the id sequence — 7854-7856 and 7859 — each one a fresh attempt paying again for the board
read the attempt before it had already completed.

It is also why a turn's steps appear while a game is live and vanish on reload. Live frames are
fire-and-forget and are never written down ([ADR-0035](0035-live-frames-are-not-events.md)); they
are meant to be superseded by the committed events moments later. When the turn rolls back there
are no committed events, so what a viewer watched happen has nothing to replace it.

The worst case is a model that cannot finish a whole turn inside one provider window. Every attempt
does real work, every attempt is discarded, and it never banks a single step — so it never moves,
however many times it is retried.

### The rollback is broader than the hazard

A rate limit is raised by `complete()`, which is called **before** the round's assistant message is
appended. So at the moment a call is refused the transcript is already at a clean boundary: the
previous round's assistant message and all of its tool results are written, and nothing partial
exists. There is no dangling tool call to avoid, because the failure happens between rounds rather
than inside one.

## Decision

**A turn keeps the rounds it completed, and the retry continues it.**

* On a provider failure the turn's rounds, `llm_calls`, `tool_calls`, events and spend are
  **committed**. The turn is marked incomplete rather than failed.
* The retry resumes **that** turn — the same row, the transcript continued from what is stored —
  rather than opening a new one. `expected_ply` still guards idempotency ([ADR-0007](0007-turn-level-jobs.md));
  the ply has not moved.
* **Only at a round boundary.** Nothing is committed that leaves an assistant message whose
  `tool_calls` have no results. This is the whole of invariant 2's protection and it is not being
  relaxed — it is being stated precisely instead of approximated by discarding everything.

### Which failures continue, and which still roll back

Five failure classes share this path today. They do not deserve the same answer, and the rule is:
**commit when the next attempt will send the same request again; roll back when it must send
something different.**

| failure | next attempt | |
| --- | --- | --- |
| `LlmError` — 429, timeout, 5xx | the same request, later | **commit and continue** |
| `NoRoomToAnswerError` | must be *smaller* | roll back |
| `HarnessCeilingError` | must be different | roll back |
| `ProviderAccountingError` | nothing; the game is abandoned | roll back |
| `ProviderMangledError` | the same bytes, same bad result | roll back |

`NoRoomToAnswerError` is the one worth being explicit about: the transcript already leaves no room
for an answer, so keeping more rounds makes the next attempt strictly worse. Continuing would be
the opposite of a fix.

### Per-turn bounds accumulate, and exhausting them is never a forfeit

`max_tool_iterations`, `max_closing_rounds` and `max_completion_budget` are per turn. A turn that
spans attempts accumulates against them, because the rounds are real and in the transcript.

**Reaching one on a resumed turn is a harness stop, not a forfeit** (invariant 11,
[ADR-0019](0019-harness-bounds-are-not-findings.md)). A model must not be written off for a bound it
reached because our provider kept refusing us; that is precisely the shape that cost `laguna-s-2.1`
a won game in [ADR-0024](0024-endpoint-output-ceilings-are-not-findings.md).

## Consequences

**The prompt grows across attempts.** A model that reads the board on each attempt accumulates
those rounds, so each continuation carries a longer prompt. Accepted: it is cheaper than paying for
the same calls repeatedly, and it is the truth about what happened. The repeated-question nudge
([ADR-0026](0026-a-repeated-question-gets-a-different-answer.md)) already answers a model that asks
the same thing twice, and compaction already answers a transcript that grows.

**The record stops lying.** Calls that happened have rows; spend appears in both ledgers; a
spectator's steps survive a reload.

**A turn is no longer one transaction.** That is the real cost of this decision and the reason it
needs an ADR rather than a patch. The invariant that replaces it is narrower and easier to check:
*a committed transcript is always sendable.* `tests/agents/test_the_invariants_hold.py` already asks
that question of a played game and gains a resumed-partial-turn case, because the failure it guards
is silent, permanent, and would otherwise be found in production for the third time.

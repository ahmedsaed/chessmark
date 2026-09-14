# 0043. A pool carries its eras, because a pool never ends

**Status:** Accepted
**Date:** 2026-09-14
**Amends:** [0027](0027-a-pool-is-ranked-by-its-own-rating.md), which made the pool an open-ended
format without saying what happens to it when the task changes. Answers for the tournament what
[0042](0042-the-notation-was-still-analysing-the-position.md) answered for the leaderboard.

## Context

In two days we reset a pool by hand three times. The slugs are the evidence:

```
pool-free  →  pool-free-v3  →  pool-free-v4
```

Each reset had a reason — v3 bumped the prompt and the tool schema, v4 stripped the mate suffix —
and each was the same operation: abandon the event, create a successor, watch a table with three
games in it sit on the front page next to eighteen days of real history.

**A pool is defined by never ending.** ADR-0027 built it that way on purpose: it re-resolves its
field every tick so a newly listed model joins by itself, it ranks by Glicko-2 rather than points
because an open population has no complete crosstable, and it has no final round. An event that gets
abandoned and recreated every time the prompt moves is not that. It is a series of tournaments
wearing a pool's name, and the version was leaking into the URL bar because it had nowhere else to
live.

### What the first attempt got wrong

ADR-0042 stamped `prompt_version` and `tool_schema_version` on the **tournament** and made a stale
event *hold* — stop starting games, and say why. That prevents the silent corruption, and it is
still the wrong answer: it makes a deploy stop the one format that is supposed to run forever, and
it leaves the operator doing the same three-step reset, only now prompted.

### And the display is not the hard part

The tempting fix is to filter the crosstable and stop there. That would leave the real damage in
place, because `results_so_far` and `attempted` are what the balance policy reads to decide who has
met whom (ADR-0041). Unscoped, the pool opens its new era convinced **every pair has already
played** — rematching immediately while models that had never met under the new rules wait. The
table would look right and the matchmaker would be wrong.

## Decision

**An era is the prompt and tool majors, joined:** `v3+v4`. Bumping either half opens a new one on
the next tick. The pool does not stop, is not replaced, and keeps its slug for good.

**Majors, so the boundary is exactly `same_task`'s.** A minor bump states the same task more
conveniently (ADR-0038), and splitting an era on one would throw away a round robin to record a
distinction the leaderboard does not make — after which a pool's table and the leaderboard could
disagree about what counts. One rule, two places.

**The era is stamped on the pairing, not derived from the game.** An *unplayed* pairing has no game
to derive a version from, and those are precisely the rows the matchmaker reads. `tournament_games`
carries it, written when the round is recorded.

**Everything the matchmaker and the table read is scoped to one era** — `results_so_far`,
`attempted`, `unplayed`. Concurrency is not: `in_flight`, `due_to_resume` and `spent` stay
event-wide, because a game that is running costs an allowance and occupies a worker whichever era
scheduled it, and finishing a game beats starting one (ADR-0025).

**A game already in flight finishes in its own era and scores there.** Ending it because the harness
was deployed over would be the harness taking a result off a model, which is invariant 11.

**A stale *unplayed* pairing is retired with a reason** — *"the pool moved on to v3+v4"* — rather
than deleted. It will never run, and a fixture on a table that nothing will ever start is a lie; but
the old era's crosstable should still show what it had planned when it ended.

**`eras_of` reads the eras from the pairings.** Which eras an event has played is a fact about what
it played, and a second copy on the tournament row could disagree with it.

**The page shows the era being played, and offers the rest.** `?era=` selects a past one, rendered
as links rather than a control — the page is a server component, and one `<a>` per era needs no
JavaScript to be correct. Silent for an event with fewer than two eras, which is every closed
tournament and every new pool: a dropdown offering one option is furniture.

## Alternatives considered

**Hold the pool on a stale task** — ADR-0042's first answer, reverted here before it shipped. It
stops the corruption and stops the pool, which is the one thing a pool is not supposed to do.

**A separate `eras` table.** More faithful to the idea, and it buys an `opened_at`, a `closed_at`
and a name. Rejected as premature: everything asked of an era today is answered by a column on the
pairing, and the table would be a second place for the truth about which era a pairing is in.

**Derive the era from each game's own `prompt_version`.** Keeps one source of truth and needs no
column — and cannot answer for an unplayed pairing, which is the row that matters most.

**Scope the display only.** Cheapest, and leaves the matchmaker reading a completed round robin
from a task nobody is playing. The bug would be invisible in exactly the place a reader would look
to check.

**Keep abandoning and recreating, and hide retired pools from the listing.** The shape we had. It
makes the tournaments page clean and leaves the model wrong: a pool that ends is not a pool.

## Consequences

**`pool-free` becomes the only free pool there is ever going to be.** It absorbs its own history as
`v1+v1`, `v2+v2` and onward; `pool-free-v3` is deleted and `pool-free-v4` is never created. A deploy
that changes the task needs no operator action at all — the pool opens an era on its next tick.

**A new era starts from an empty crosstable**, which is the point and will still look abrupt: the
day after a bump, the table is nearly empty and every rating is provisional again. The previous era
is one click away, which is the least a reader is owed.

**Pairings from before this column are `NULL` and stay that way.** The migration backfills only
where a game exists to read a version from, so `pool-free` sorts itself into real eras;
`close_stale_pairings` skips a `NULL` era deliberately, so the last pool's leftovers are not retired
by a migration that was not asked to make that judgement.

**Nothing enforces that a tool-output change moves `TOOL_SCHEMA_VERSION`.** An era boundary is only
as good as the version bump that triggers it, and that bump is a judgement written in an ADR — the
same standing risk ADR-0042 records for the leaderboard, now with a second consumer.

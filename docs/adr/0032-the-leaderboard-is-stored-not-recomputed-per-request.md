# 0032. The leaderboard is stored, not recomputed on every request

**Status:** Accepted
**Date:** 2026-09-06
**Amends** `bench/service.py`'s standing decision to recompute per request. The eligibility rules,
the engine and the exclusions are unchanged; only *when* they run moves.

## Context

The leaderboard was rebuilt from raw rows on **every request**, and four pages await it: `/`,
`/about`, `/methodology` and `/leaderboard`. Two of those display no rating at all. `/methodology`
renders three scalars — how many games counted, how many did not, and the prompt version — and
links to `/leaderboard` for the exclusions themselves. `/about` renders one.

For those, a request ran the full eligibility scan over every finished game, a Glicko-2 pass over
every rating period, the per-contestant aggregates, and a registry lookup — then discarded every
row it had built. A page of prose paid for the whole ranking.

The original decision was deliberate and is quoted here because it is still right about the thing it
cared about:

> Computed on request rather than read from a cache. Ratings are a pure function of the games that
> produced them, and a stored value that drifted from that function would be undetectable — which is
> exactly the property the determinism criterion is about. […] When the game count makes that
> untrue, the fix is a cached run with a recorded input hash, not a mutable table.

Two things had changed since it was written.

**The economics were never revisited.** Games reach a terminal state roughly **ten times a day** at
peak. The pages that read the ranking are read on every visit. The computation was on the side of
the ledger with three orders of magnitude more traffic.

**The storage was built and never connected.** `store_ratings()` exists, the `ratings` table exists
and is migrated, and *nothing calls the first or reads the second*. So "computed per request" had
stopped being a trade-off and had become the only path, beside a dead table.

## Decision

**Compute once per change, not once per request. Store it. Serve the stored result.**

- The run is written to `leaderboard_snapshots` and read from there. No scan, no Glicko-2, no
  aggregates on a request.
- Display names are resolved **at read** from `model_registry`, so renaming a model does not
  require a rebuild and cannot leave a snapshot showing a stale label.

**The rebuild is triggered by the first read that notices, not by the transaction that ends the
game.** That is deliberate. Ending a game is the one write that must not fail: the game record is
authoritative and must never be corruptible (invariant 1), and the worker holds one transaction per
turn (ADR-0007). Rebuilding a *cache* inside that transaction would let a derived, disposable value
roll back a real result. Nothing that can fail a game may be added to it for the benefit of a page.

The cost of that choice is one slow request per game ended — around ten a day — against a page load
that was slow every time. Every subsequent read is a row.

This is not an incremental update, and Glicko-2 will not permit one: it is defined over rating
periods, so a new game moves both players' current period *and* widens the deviation of every
contestant idle since. The stored value is the same full run the request used to do, moved to the
side of the ledger where it happens ten times a day instead of continuously.

### Staleness is detected, not assumed away

The original objection — a stored number that quietly stops matching its games — is answered
directly rather than by recomputing forever. Every snapshot records a **fingerprint of its inputs**:

- the number of terminal games, and the latest `ended_at` among them
- the number of `model_endpoints` rows and the latest id, since a precision decides a contestant's
  identity (ADR-0015)
- the prompt version the run was computed for

A read compares the fingerprint against the database. **A mismatch recomputes inline and stores the
result**; it never serves the stale row. This is what makes the trigger question moot: there is no
list of call sites to keep in sync, so no path can end a game and forget to invalidate. A snapshot
lost to a crash, a truncated table, a restored backup or a hand-edited row all resolve the same way
— one slow request, then correct. **The failure mode is a slow page, never a wrong number.**

That is the "cached run with a recorded input hash" the original decision named as its own escape
hatch, and it keeps invariant 3: a published number and the games behind it cannot disagree.

## Consequences

- The four pages stop paying for a ranking they mostly do not show. The read path becomes two cheap
  indexed aggregates and one row.
- **No new failure mode on the write path.** Nothing about ending a game changed, so no game can be
  lost to a cache.
- **The determinism criterion still holds**, and is now cheaper to assert: a test recomputes from
  scratch and compares against the stored snapshot, which is a stronger check than recomputing twice
  in one process.
- The snapshot is a **cache, not a source of truth.** It is derived, disposable, and rebuilt from
  `games` at any time. Nothing may read it to decide anything a game record could answer.
- `store_ratings()` and the `ratings` table are removed. A normalised per-period table was the shape
  the dead code assumed, and it cannot hold what the response actually needs — the exclusions with
  their reasons, the aggregate metrics, the counted set behind each row. One snapshot holds the run
  that produced all of them, which is also what makes the fingerprint meaningful.
- Tournament ratings (`ratings_by_key`, ADR-0027) are **not** cached here. They are scoped to one
  event and read only on that event's page; the same treatment can follow if it is ever warranted.

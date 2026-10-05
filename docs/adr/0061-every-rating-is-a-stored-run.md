# 0061. Every rating is a stored run, read rather than computed

**Status:** Accepted
**Date:** 2026-10-05
**Amends:** [0032](0032-the-leaderboard-is-stored-not-recomputed-per-request.md). The leaderboard's
stored, fingerprinted run becomes the only way a rating is produced for the site, and a pool's
ratings get one of their own.

## Context

After [ADR-0060](0060-models-are-rated-by-bradley-terry-and-ranked-by-proven-strength.md), three
places produced a rating, and only one of them was stored:

- **The leaderboard** read a stored run, rebuilt on the first read whose fingerprint disagreed
  with the games (ADR-0032).
- **A pool's standings** fitted the pool's ratings on every API request.
- **The matchmaker** fitted ratings over every counted game, every time it scheduled one.

None of them was slow: the fit is about 18 ms, and the site's cache means the standings were
computed a handful of times per game. The problem was that the same number had three code paths,
two of them running on demand. A rating the matchmaker paired on was not, by construction, the
rating the site showed.

**And the leaderboard's stored run had never been stored from a request.** A request's session is
opened per request and never committed, so a rebuild written through it was rolled back when the
request ended. Every read of the leaderboard rebuilt it from scratch for as long as ADR-0032 had
been live. Every test passed, because the bench tests share one session across calls and read
their own uncommitted write. It surfaced only because this change was checked against the table
itself, which was empty after a page had been served.

The standings endpoint also read each game's endpoints one game at a time, so its cost grew with
the event. That was 84 reads for `pool-free`, on a page CLAUDE.md says should do fixed work.

## Decision

**Every rating is a stored run, in one table, kept honest one way.**

- `leaderboard_snapshots` gains a `scope`: `""` for the leaderboard, `tournament:<id>` for one
  pool's own games. The unique key is `(prompt_version, scope)`.
- `snapshot.current` serves the leaderboard as before.
- `snapshot.pool_ratings` serves a pool's ratings in the same way: read, check the fingerprint,
  rebuild only if it disagrees.

**A pool's fingerprint covers that pool's games.** A game finishing in another event does not
rebuild it. The other inputs are the same as the leaderboard's: endpoints, prompt version, decision
version and rating method.

**What is stored for a pool is the rating and its deviation.** `provisional` and `proven` are
derived on the way out, as ADR-0028 requires, so they cannot drift from the numbers they describe.

**The matchmaker reads the leaderboard's stored run.** It pairs on exactly what the site shows.

**A run is written in a transaction of its own, and committed.** It belongs to neither caller. A
request's session is never committed. The matchmaker's tick must not have half its work committed
by a cache write. A failed write is logged and the computed run is still served: losing the cache
costs the next reader a rebuild, while raising would cost this reader the page. An endpoint-level
test, where each request has its own session as in production, now asserts that the second read
builds nothing.

**The fingerprint is taken before the build, not after.** Taken after, a game ending while a run
was being computed was in the fingerprint and not in the run, so the incomplete run matched and was
served as current until another game ended. This was latent in ADR-0032's code. It surfaced while
generalising it, and a test now holds it.

**Rebuilds still happen on a read, never when a game ends** (ADR-0032). A derived value must not
be able to roll back a real result.

**The standings read every game's endpoints in one query.** A warm read of a pool costs the same
whatever the number of games, and a test asserts it.

## Alternatives considered

**A separate `pool_rating_snapshots` table.** Purely additive, with no change to an existing
constraint. Rejected: it is two tables for one concept, and the code would grow two read paths to
keep honest instead of one.

**Rebuild every scope when a game ends.** It is eager rather than lazy, so the first reader never
pays. Rejected for ADR-0032's reason: it puts a derived computation inside the path that records a
result.

**Leave it.** Measured, it was cheap. But "cheap enough" was the argument for each of the three
paths, and it was how they multiplied.

## Consequences

- **A migration** adds `scope` (default `""`, so the existing row stays the leaderboard's) and
  replaces the one-row-per-prompt unique key. The downgrade drops pool rows first. They are a cache,
  and they would collide under the old key.
- **A pool's first read after one of its games ends pays for the fit.** Every other read is a row
  lookup. Measured warm on production's data: `GET /leaderboard` went from about 50 ms to 8 ms,
  and `GET /tournaments/pool-free` from about 82 ms to 22 ms.
- **The matchmaker's tick may rebuild the leaderboard** if it is the first reader after a game
  ended. It already computed a full fit on every tick, so this is less work, not more.

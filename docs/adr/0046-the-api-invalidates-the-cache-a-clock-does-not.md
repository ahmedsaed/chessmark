# 0046. The API invalidates the frontend's cache; a clock does not

**Status:** Accepted
**Date:** 2026-09-18
**Supersedes** the standing decision recorded in `lib/api.ts` and
[FRONTEND.md](../FRONTEND.md#streaming-and-the-price-of-it) that every route is `force-dynamic` and
nothing a person waits on is cached. The social cards' own `revalidate` (ADR-era, `REVALIDATE_SECONDS`)
is unchanged and still correct.

## Context

Every page route exported `export const dynamic = "force-dynamic"`, and `lib/api.ts` opened with the
sentence that justified it:

> In Next.js 16 `fetch` is **not** cached by default, so nothing here needs `no-store` — a live game
> page reads fresh data on every request without asking.
>
> Nothing a person waits on caches; a picture of a page does.

That rule was applied to four different kinds of data as if they were one:

| | example | actually |
| --- | --- | --- |
| genuinely live | a running game's board | must **not** be cached, and this was got wrong once — see *What we reject* |
| immutable forever | a finished game | can never change again, re-rendered from scratch on every request anyway |
| changes on a known event | leaderboard, models, tournaments | changes when a game ends — a signal the API *has* (invariant 7), not a clock |
| does not change | `/about`, `/methodology` prose | `force-dynamic`, plus a full-page skeleton, for three integers |

The blanket rule was wrong about the three rows below the first, which is most of the site: the
archive is permanently immutable and most of its URL space, and got nothing.

The cost was not theoretical. Because the render blocked on live reads, a click produced no feedback
until the whole render finished, and the fix for *that* was a `loading.tsx` skeleton on four routes
and five `<Suspense>` boundaries on the lobby. Measured on a development machine against 106 games
and 286 models, `/` sent its first byte at 4.4ms and did not finish until 43.1ms: **39 milliseconds
of assembling itself in front of the reader.** The skeleton was not a fast page; it was a slow page
wearing a costume, and one of the skeletons did not even match the shape it stood in for.

The second problem was that a `revalidate` measured in seconds is a *guess* about a moment the
system already knows exactly. Every one of these reads changes when a game writes a `game_events`
row. Too short a number and the cache buys nothing; too long and the leaderboard is wrong for as
long as the number says. There was no correct number, because time was the wrong instrument.

## Decision

**Cache every public read, tag it, and let the API say when a tag is stale.**

1. **`force-dynamic` is removed from all ten page routes.** It is not merely unnecessary, it is
   actively hostile to this: it is documented as equivalent to setting every `fetch` in the segment
   to `{ cache: 'no-store', next: { revalidate: 0 } }`, so tags attached under it would have looked
   like caching while caching nothing.

2. **Every public read in `lib/api.ts` carries `next: { tags, revalidate }`.** The tag vocabulary is
   spelled once in `lib/cache-tags.ts` and imported by both ends. Per-game reads carry `game:<id>`
   as well as `games`, so a move in one game cannot expire the archive.

   **Except a game that can still move.** `getGame` caches only when the caller passes
   `settled: true`, and `listEvents`/`listTurns` never cache. A caller that does not know gets the
   live read, because the dangerous direction is serving a stale board to somebody watching it.

3. **The seconds are a backstop, not the mechanism.** `FALLBACK_REVALIDATE` is five minutes and
   exists only to bound how long a *lost* notification can leave a page stale.

4. **The worker names the tags.** `orchestration/revalidation.py` posts them to
   `POST /api/revalidate` on the web tier, from inside `publish_events` — the function that already
   exists to tell the outside world about committed events — and from `_settle`, which is the one
   path a person's own move takes. `revalidateTag(tag, "max")` marks the entry stale and serves the
   old answer while the new one is fetched, so a game ending never puts a visitor in a queue behind
   our own cache miss.

5. **The skeletons are deleted.** Four `loading.tsx` files, `PageSkeleton`, and the lobby's five
   `<Suspense>` boundaries. The lobby still fetches in parallel — that is what the boundaries were
   really providing — but renders in one pass.

## What this buys, measured

Time to the **last** byte, which is when the page is actually complete, median of 15 requests:

| route | before | after |
| --- | --- | --- |
| `/` | 43.1ms | 13.1ms |
| `/leaderboard` | 20.4ms | 4.4ms |
| `/about` | 18.6ms | 3.9ms |
| `/methodology` | 16.6ms | 4.1ms |
| `/models` | 26.1ms | 11.9ms |
| `/tournaments` | 15.1ms | 3.9ms |
| `/play` | 21.5ms | 6.7ms |

The number that matters most is not in that table: **first byte and last byte have converged.** On
`/` they were 4.4ms and 43.1ms; they are now 14.1ms and 14.2ms. The page arrives whole. Five
consecutive loads of `/leaderboard` now cause **zero** reads of `/leaderboard` on the API, against
five before.

## What we reject, and live with

**A cached page can be stale.** If the revalidation POST is lost — the web tier is down, the secret
is wrong, the network drops it — a page is stale until the five-minute fallback. This is the price
of the design and it is bounded on purpose. A 401 from the endpoint is logged as a warning rather
than swallowed, because the symptom (a leaderboard a few minutes behind) looks like nothing at all.

**The endpoint is a cache-eviction lever pointed at our own site.** It is defended twice: a
constant-time comparison against a shared secret, and an allowlist — a caller holding the secret
still cannot name `*`. **Unset means refuse, not allow**, so a deployment that forgets
`REVALIDATE_SECRET` gets a 503 rather than an unauthenticated eviction endpoint.

**A running game must never be cached, and the first version of this change cached it.** The
argument was that a live board is self-correcting because `EventStream` delivers moves over SSE.
That is false in the one way that matters: the **first paint** comes from the server read, and
`revalidateTag(tag, "max")` is stale-while-revalidate, so the reader after a move is handed the
position from *before* it and the stream only carries what happens next. `play.spec.ts` caught it
exactly — a person moved, the board stayed on the opening position, and five signed-in tests failed
together. `getGame` now caches only on an explicit `settled: true` from a caller that can promise
the record has stopped; `listEvents` and `listTurns` never cache.

The general lesson is worth more than the fix: **"something else will correct it" is not an argument
for caching.** It says the wrong answer is temporary, not that it is never shown.

**A settled game is safe under invariant 8 *only because* `must_withhold_thinking` is a function of
`(status, has_human_player)` and not of the viewer.** Every reader of a given game is served
identical bytes, so a shared cache entry cannot leak a live opponent's reasoning to somebody not
already entitled to see it. **If that rule ever becomes per-viewer, even the settled reads must stop
being cached on the same day.** This is stated in `lib/api.ts` too, where somebody changing the
redaction rule might actually read it.

**Per-user reads are never cached, and the line is drawn at the function boundary.** `/me` and
`/games/mine` answer differently depending on who asks; they go through `post`/`listMyGames`, which
carry a Clerk token and never touch the cached `get`.

**A cold cache is slower than the old uncached path**, because the page now awaits everything rather
than streaming what it has. Stale-while-revalidate makes this a first-visit-after-deploy cost rather
than a recurring one, and 13ms warm against 43ms warm is the trade.

### Rejected: Cache Components (`cacheComponents: true`)

Next.js 16's PPR would prerender a static shell per route. It was tried and reverted, and the
reasons are recorded rather than the conclusion alone:

* **It is the streaming architecture again.** Under it *every* dynamic route streams a shell first
  and fills in behind — which is the assembling-in-front-of-the-reader this ADR exists to remove.
  It only becomes strictly better where a page's data is *entirely* cached, and getting there means
  the two items below.
* **It breaks the 404 contract site-wide.** Next's own docs are explicit: "With Cache Components,
  every dynamic route streams a static shell first, so run that check in `proxy` instead." The three
  routes that can `notFound()` are asserted at `site.spec.ts:175`, and their existence checks would
  have to move into the proxy — an API round trip in middleware on every game, model and tournament
  request.
* **It costs the 87 KiB Clerk saving, or an auth refactor to keep it.** `cacheComponents` rejects
  the root layout's `cookies()`/`headers()` read, which is what decides whether `ClerkProvider`
  mounts. Restructuring that touches authentication on every page — and the browser suite's
  signed-in half does not run in CI (ROADMAP, *Known gaps*), so the tests that would catch a
  regression are the ones nobody runs on a pull request.

The measured headroom over this ADR is single-digit milliseconds of server render. It is recorded in
ROADMAP's *Known gaps* rather than done.

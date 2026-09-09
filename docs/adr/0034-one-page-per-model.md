# 0034. One page per model, and the contestant is a section on it

**Status:** Accepted
**Date:** 2026-09-09
**Amends:** the drill-down shipped in Phase 12, which made
`/leaderboard/{slug}?q={precision}` a page of its own. Reinforces
[0015](0015-endpoint-pinning-and-quantization.md) — the contestant is still `(model, precision)`,
it is simply no longer a separate URL — and
[0032](0032-the-leaderboard-is-stored-not-recomputed-per-request.md), which this extends to the
model page.

## Context

Two pages described the same model.

`/models/{slug}` had the registry facts, the endpoints, the precisions it is served at, and a
**record over every game** — exhibitions, human games and ranked games alike. `/leaderboard/{slug}
?q=fp8` had one contestant's rating and the **ratable games** behind it (BENCH-02).

Both printed a heading that said `W / D / L`. The numbers under them were different, because the
sets of games were different, and neither page said so. Which pair a reader saw was decided by
where they came from: the leaderboard and the landing hero linked to one, the tournament table and
the model catalogue to the other. A reader who found both had no way to tell whether they had
discovered a distinction or a bug — and the question that surfaced this was exactly that: *"it says
6 / 5 / 4, which is fifteen, and the page lists eight."*

That report turned out to be an unrelated indexing fault, fixed separately. But looking for it took
a lot longer than it should have, because "which of this model's two W/D/L figures is wrong" is not
a question the product should ever have been able to raise.

The split had a second cost. `get_model` recomputed the ratings and the aggregates on every
request, without sharing a scan — **two full sweeps of the archive per page view**, the exact cost
ADR-0032 had just removed from the leaderboard. Nothing measured that route, so nothing said.

## Decision

**A model has one page, and it is `/models/{slug}`.** `/leaderboard/{slug}` permanently redirects
to it, `?q=fp8` becoming the anchor `#c-fp8`.

**A contestant is a block on that page, not a page.** Each precision carries what serves it, the
rating it holds, and the ratable games behind that rating — the whole of what the drill-down was.

**The three scopes are shown side by side and named.** The record over every game, the ratings over
the ratable ones, and — this is the part that was missing everywhere — **the difference, itemised
with a reason per game** (BENCH-10). Two W/D/L figures on one page is honest only if a reader can
see what separates them; two W/D/L figures on two pages was never honest at all.

**The page reads the stored run** (ADR-0032), and a query-count test holds it there.

### The redirect is in `next.config.ts`, not in a route

`app/leaderboard/loading.tsx` covers that segment and everything under it. A streamed response
commits its status line before the page body runs, so a `permanentRedirect()` from the route would
have been served as a `200` with the navigation happening client-side — a crawler or a link checker
would never see the `308`. This is the same trap that once turned every missing game, model and
tournament into a `200`
([FRONTEND.md](../FRONTEND.md#streaming-and-the-price-of-it)). A config redirect runs before
routing, so it answers with the real status.

## Consequences

A published `/leaderboard/{slug}?q=` link still lands on what it named, one segment deeper.

`GET /leaderboard/{model_slug}/games` stays. The site no longer calls it — the page partitions the
games it already holds, using the ids the model payload now carries — but it is the documented
BENCH-02 drill-down for anyone reading the API, and it is served from the same stored index, so the
two cannot disagree.

The model page grew: a model with many games renders all of them, in three groups. That is the
intent — everything known about a model is on the page about it — and if it ever needs paging, it
pages there rather than by splitting the subject across URLs again.

A rating and a record can still differ. They are *supposed* to. What is no longer possible is for
them to differ without the page saying which games account for it.

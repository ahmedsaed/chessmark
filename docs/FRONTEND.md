# Frontend

Next.js 16 App Router, in `apps/web`. Server Components by default; `"use client"` only where
interactivity demands it. Tailwind for styling.

> ⚠️ **Next.js 16 differs from older Next.js.** `apps/web/AGENTS.md` is auto-generated and says so.
> Before writing frontend code, read the relevant guide under
> `apps/web/node_modules/next/dist/docs/`. Do not rely on Next.js knowledge from memory.

`pnpm exec next typegen` must run before `tsc` — Next.js 16 generates the global route types
(`LayoutProps`, `PageProps`) that app code depends on. `make check` does this for you.

## Design

The system is settled: [ADR-0013](adr/0013-design-system.md). Tokens live in
`apps/web/src/app/globals.css` as Tailwind `@theme` variables. **No component hard-codes a colour** —
always read a token. Dark only; there is no light theme.

The live game layout is **stats left, board centre, event stream right**, with finished turns folded
and the live turn expanded.

**The container sets the line length; a paragraph does not set its own.** There are two page widths
and the choice between them is the decision:

* **760px** — `/about`, `/methodology`, anything meant to be *read*. The prose fills the column,
  and the column is already a comfortable measure.
* **1180px** — the leaderboard, the catalogue, a model, a tournament, the lobby. These are pages of
  tables, grids and cards. Their prose is a sentence or two introducing the block underneath it.

Capping prose *again* inside the wide one — `max-w-prose`, 65ch, a little over half of 1180 — was
the wrong tool in the right place. It stopped the paragraph at an edge that lines up with nothing:
every table, grid and card below it runs the full width, so the text read as a column that had lost
its other half. The fix for a paragraph that is too wide to read is **a narrower page**, not a
narrower paragraph in a wide one.

Two deliberate exceptions, and they are the same exception: a bound that is doing something other
than setting a measure. The empty hero's paragraph is centred in a centred box, so it is bounded to
stay centred; the footer blurb is one column of a flex row, so it is bounded to leave room for the
nav beside it.

## The right-hand column is `EventStream`, not `Conversation`

It was named for trash talk and had long since stopped being that: it carries reasoning, output, tool
calls, illegal attempts, and the harness interrupting itself.

**Reasoning, output and tool calls each have their own disclosure**, and output is closed by default.
One fold per turn meant reading a tool call also unrolled several thousand words of reasoning, so the
thing you wanted was pushed off screen by the thing you did not. Each trigger carries a size hint
(`reasoning · 2.4k`) because the question a reader is asking is whether it is a glance or a scroll.

Illegal attempts unroll with the tools — an illegal move *is* a failed `make_move` — and `raw`
belongs to the turn, so it is reachable while everything else is folded.

**A pause renders as a notice** — full width, no side — because a rate limit is not something either
contestant did, and drawing it as a player's message would attribute the harness's failure to a
model. Notices belong to no turn (the failed turn is rolled back whole, so its `turn_started` never
reaches the log) and are interleaved with the turns by `seq`.

**A withheld reasoning trace is not an absent one.** `api/redaction.py` strips the text from a game
its reader is playing and keeps the token count (invariant 8), and the panel used to render that
identically to a model that had said nothing — a turn showing only its tool calls, with no hint that
anything was held back. `withheldReasoning` carries the count, so the turn says `thinking · 801`
without saying what about.

## Replay reuses the live view

A finished game is scrubbable ply by ply, with the raw provider payloads behind every turn one click
away. Replay **truncates the event log and reuses the live view's fold**, so the two cannot drift
([ADR-0008](adr/0008-game-events-log.md)).

## Promotion is chosen, not assumed

A human drag to the last rank opens a picker. It used to be a queen either way, which is right almost
every time and wrong in exactly the position that matters: the one where a rook or a knight wins and
the player cannot say so.

## A turn arrives twice

The panel receives two kinds of frame, and only one of them is the record ([ADR-0035](adr/0035-live-frames-are-not-events.md)).

**Committed events** are numbered, stored, and replayed on reconnect. They arrive when the turn's
transaction commits — which is *after every provider round has finished*, because a turn is one
transaction ([ADR-0007](adr/0007-turn-level-jobs.md)). Ply 8 of `e601f9af` spent 632 seconds across
six rounds and delivered all fifteen of its events stamped the same millisecond.

**Live frames** arrive as each round lands, on the `delta` SSE event. They carry no `seq`, are
never stored, and are dropped by `useGameStream` at the next `turn_started`. `liveTurn` folds them
into a provisional `TurnView` that is drawn exactly like a real open turn — because to a reader it
*is* the open turn; the record has simply not caught up.

Three things follow, and each has bitten:

* **A live frame must never carry an `id:`.** `Last-Event-ID` is how a reconnect resumes, and it
  has to name a committed event or the client resumes from something that was never written down.
* **Provisional blocks key negative.** React keys on `seq`, which is 1-based and gap-free, so a
  provisional block sharing a number with a committed one would hand a prediction the DOM of a
  fact.
* **A turn announces itself twice too.** `turn_started` is inside the transaction like everything
  else, so without a `turn` frame the blocks describe a turn nothing has announced.

A component that ignores `delta` entirely is correct and complete: replay, the PGN and every test
that reads the log are unaffected.

## Streaming, and the price of it

Every route is `force-dynamic` — a live game and a leaderboard are both wrong the moment they are
cached — so without a Suspense boundary a click produced no feedback at all until the whole server
render finished. No spinner, no route change. Several hundred milliseconds of that reads as a broken
link rather than a slow one.

Two ways to yield, and they are not interchangeable:

- **`loading.tsx`** wraps the segment **and every segment under it**. Cheapest for a leaf route.
- **`<Suspense>` inside a synchronous page** yields per section, so a slow ranking delays only the
  ranking. `app/page.tsx` is the worked example: the page component is not `async`, and each section
  is its own async component. Next.js memoises `fetch` for the request, so sections asking for the
  same list read one in-flight promise rather than two.

**A `loading.tsx` above a route that can `notFound()` turns its 404 into a 200.** The boundary makes
the segment stream, and a streamed response commits its status line before the page body runs — so
the not-found page renders under a `200` and every link checker and crawler believes it. A blanket
`app/loading.tsx` silently did this to every missing game, model and tournament; nothing in a
browser looks wrong. Only routes that **cannot** 404 have one, and `site.spec.ts` asserts the status
for the three that can. A route that 404s should resolve that check first and stream what comes
after it.

## Traps

**The site must load without Clerk keys.** `src/proxy.ts` called `clerkMiddleware()`
unconditionally, and it throws without a publishable key — a throwing proxy takes every route with
it, so `/`, `/about`, `/leaderboard` and `/play` all answered 500 on a fresh clone. `AuthProvider`
and `AccountBar` both degraded correctly and it made no difference. **A guard downstream of a
throwing proxy is not a guard.**

**An empty environment variable is not an absent one.** `??` does not cover `""`, and an empty
`NEXT_PUBLIC_API_URL` produced a build that fetched from nowhere. `lib/env.ts` (`originFromEnv`)
is the one place that decides, and it trims and falls back on blank.

**Client-side filtering is the point on `/models`.** It filters the whole catalogue with zero
requests across nine keystrokes — measured, and asserted by the browser suite. Note that not every
URL containing `/models` is an API call: a router prefetch is not a request the page made.

## Testing

Logic in `src/lib` is unit-tested with `vitest` (`make test-web`); coverage is measured *and*
enforced (`make test-web-coverage`, NFR-10). Components are covered by Playwright rather than a jsdom
stack. See [TESTING.md](TESTING.md).

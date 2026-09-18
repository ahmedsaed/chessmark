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
and the live turn expanded. `GameLayout` owns that arrangement and the phone one below it.

**The small end of the type scale is named, and no component sets a size in pixels.** Four steps,
named for what they are for, so choosing one is a question with an answer:

| | | |
| --- | --- | --- |
| `text-label` | 9px | uppercase, tracked section headings and badges |
| `text-meta` | 10px | secondary supporting text — ids, timestamps, captions, hints |
| `text-data` | 11px | dense mono figures — ratings, counts, token totals, nameplates |
| `text-chat` | 13px | what a player said, in a bubble |

They replaced **190 arbitrary values** (`text-[10px]`) spread over six sizes — 8.5, 9, 9.5, 10, 11
and 13px, three of them within a pixel of each other. That is not a scale, it is a set of one-off
decisions, and it had a measurable cost: the phone floor below was written as an attribute-substring
selector matching on class *names*, because there was no token to redefine. `8.5` and `9.5` are
gone; a half-pixel does not survive rasterisation, and keeping them meant three names for one step.

**The phone floor is the scale, redefined.** Below `sm` the three smallest steps are all 11px —
comfortable on a monitor at arm's length is marginal on a phone at reading distance, and those steps
are most of what a phone reader is given. Tailwind v4 compiles `text-meta` to
`font-size: var(--text-meta)`, so this is four lines in a media query rather than an override per
utility, and `layout.spec.ts` asserts nothing visible renders below 11px at 390px.

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

## The game page is three columns, and two tabs

`GameLayout` owns the shape, and `LiveGame` and `Replay` both hand it a board, a stream and a rail.
They drew the same grid character for character until a change had to be remembered into both.

Wide, nothing has changed: one `min()` expression sizes the board from whichever runs out first, the
height under the page chrome or a share of the width, and the two rails split the rest.

**On a phone the three columns become a board and two tabs.** Stacked, the order was board,
conversation, stats — so the stats sat a whole conversation below the board, on the one screen size
where a scroll costs most, and *whose move it is* and *what it has cost* were the least reachable
things on the page. Both panels stay mounted and are hidden with CSS: `EventStream` holds the
reader's scroll position and which turn is expanded, and unmounting it would hand back a panel
scrolled to the top every time somebody checked the stats.

The conversation is **bounded** there, at `58svh`. Left to grow it was as tall as the game was long
— 300 plies of it — so reaching the newest turn meant scrolling past every older one. It already
scrolled inside itself; it only ever needed to be told how tall it is. `svh` rather than `vh`
because a phone's URL bar makes `vh` taller than the screen.

Held by the `mobile` browser project (UI-11), not by eye — see
[TESTING.md](TESTING.md#the-browser-suite).

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

**Arriving in the middle of a turn.** Pub/sub is fire-and-forget, so a spectator who opens a game
nine minutes into a round would get the committed backfill — everything up to the *last* turn —
and then a still board until this one commits. The in-flight turn's frames are therefore kept in a
Redis list and replayed after the backfill, so a latecomer is caught up to the same place as
everyone already watching. A new turn clears that buffer; a TTL collects what a rolled-back turn
leaves behind.

**Many viewers.** Redis fans a frame out to every subscriber, so an audience costs no more than a
single reader and all of them see the same thing. What is decided *per connection* is
`must_withhold_thinking`: two people can watch the same game and one of them — the one playing it —
is shown no reasoning at all (invariant 8).

## Caching, and why nothing streams any more

**Every public read is cached and tagged, and the API says when a tag is stale** (ADR-0046). The
tag vocabulary is `lib/cache-tags.ts`, attached by `lib/api.ts` and acted on by
`app/api/revalidate/route.ts`; the other end is `orchestration/revalidation.py`. The `revalidate`
seconds beside the tags are a **backstop bounding a lost notification**, not the mechanism — a
clock is a guess about a moment the API already knows exactly.

This replaced `force-dynamic` on all ten page routes, four `loading.tsx` skeletons and the lobby's
five `<Suspense>` boundaries. Those existed because the render blocked on live reads, so a click
produced no feedback until it finished; with the reads cached, the whole page renders in about the
time the shell alone used to take. Measured on 106 games: `/` went from first byte 4.4ms /
complete 43.1ms to first byte 12.9ms / complete **13.1ms**. First and last byte have converged,
which is the actual goal — the page arrives whole rather than assembling itself in front of the
reader.

Three things to know before changing any of it:

* **`force-dynamic` is not a neutral safety net.** It is documented as equivalent to
  `{ cache: 'no-store', next: { revalidate: 0 } }` on every `fetch` in the segment. Adding it back
  to a route silently un-caches that route's reads while the tags stay attached and keep looking
  like caching.
* **A route's own `revalidate` is a ceiling, not a setting.** Next takes the shortest life among
  the segment's `revalidate` and every `fetch` inside it. `sitemap.ts` says `3600` and silently
  became five minutes the moment its reads carried the site fallback; it now passes
  `{ cache: SITEMAP_LIFE }` explicitly. The build's route table is where this shows.
* **A per-user read must never be cached.** `/me` and `/games/mine` answer differently depending on
  who asks. They go through `post`/`listMyGames`, which carry a Clerk token and never touch the
  cached `get`.
* **Neither may a game that can still move.** `getGame` caches only on an explicit `settled: true`;
  `listEvents` and `listTurns` never do. The first version of this cached them on the argument that
  a live board is self-correcting over SSE — but the first paint comes from the server read, and
  stale-while-revalidate hands the reader the position from *before* the last move while the stream
  carries only what happens next. `play.spec.ts` failed five ways at once. A settled game is
  immutable and cached hard; it is safe under invariant 8 only because `must_withhold_thinking`
  depends on `(status, has_human_player)` and not on the viewer.

### The 404 trap, which is still live

**A `loading.tsx` above a route that can `notFound()` turns its 404 into a 200.** The boundary makes
the segment stream, and a streamed response commits its status line before the page body runs — so
the not-found page renders under a `200` and every link checker and crawler believes it. A blanket
`app/loading.tsx` silently did this to every missing game, model and tournament; nothing in a
browser looks wrong. There is now no `loading.tsx` in the app at all, and `site.spec.ts:175` asserts
the status for the three routes that can 404, so reintroducing one fails there rather than in
production.

This is also why **Cache Components (`cacheComponents: true`) is not enabled.** Under it *every*
dynamic route streams a static shell first — Next's own docs say to move the existence check into
`proxy` as a result — which would both reintroduce the assembly this section describes removing and
put an API round trip in middleware. It was tried and reverted; ADR-0046 records the full reasoning
and ROADMAP's *Known gaps* carries what is left on the table.

## Metadata, icons, and the sitemap

`lib/site.ts` is the single source: name, tagline, description, both navs, and `staticRoutes` —
the list the sitemap is built from. The root layout carries `metadataBase`, the title template,
and the site-wide OpenGraph block; `app/opengraph-image.tsx` draws the card.

**Metadata keys are inherited wholesale.** A segment that does not set a key gets the parent's
value verbatim. That cuts both ways and is the thing to know before editing any of it:

* It is why pages that set only `title` all shipped the *root's* OpenGraph block for months —
  sharing `/leaderboard` produced a card describing the site root. `pageMetadata` in `lib/site.ts`
  fixes that in one place; a page passes its title, description and path and gets its own card.
* It is also why **`alternates.canonical` must never go on the root layout.** Inherited, it tells
  a crawler that every route in the site is a duplicate of `/`. Each page states its own, and
  `/games/[id]`, `/models/[...slug]` and `/tournaments/[slug]` build theirs from the *record's*
  id rather than the URL — a model is reachable under more than one spelling of its slug, and an
  era is a view of one event (ADR-0043), not a page of its own.

**A page either colocates an `opengraph-image` or names one.** The same inheritance rule bit a
second time and harder: setting `openGraph` replaces the parent's *whole* block, including the
`images` the root's `opengraph-image.tsx` injects — so `pageMetadata`, written to stop every page
sharing the root's card, shipped seven routes with **no card at all**. `/sign-in` is the control
that identified it: it sets no metadata, so it replaced nothing, and it was the only page besides
`/` that kept its image. A file in the *same* segment outranks the page's `openGraph`; an inherited
one does not. `site.spec.ts` asserts every public route has exactly one `og:image`, which is the
only thing making the "or names one" half safe.

**A card cannot live inside a catch-all segment.** Next.js refuses
`models/[...slug]/opengraph-image.tsx` — *"catch all segment must be the last segment modifying the
path"* — because the image is served below a segment that has already claimed everything below it.
That card is a Route Handler at `/og/model/<slug>` instead, named from `generateMetadata`.

**A config redirect runs before routing, so it can swallow a card.** `/leaderboard/:slug →
/models/:slug`, written for the old contestant URLs, matched `/leaderboard/opengraph-image` and
served the *models* card for the leaderboard — 200, valid PNG, wrong picture. The redirect excludes
the route name now, and the test fetches each card with redirects disabled, because following one
lands on a perfectly good card and proves nothing.

**Cards are revalidated, not rendered per request.** Every card reads live data, and an unfurl is
not traffic anybody is waiting on: `REVALIDATE_SECONDS` in `lib/og/theme.ts` is the one clock they
all share. A card is a picture *of* a page, so it can afford to be staler than the page is.

**`themeColor` lives in a `viewport` export**, not in `metadata` — Next.js 16 errors on it there.

**The icons are the header's `Mark`**, the 3×3 checker, in the header's own board colours and in
literal hex: a favicon is fetched outside the document, so a `var(--color-*)` resolves to nothing
and the icon renders empty. `favicon.ico` and `apple-icon.png` are rasterised from `icon.svg`;
regenerate all three together, and give `apple-icon.png` no corner radius — iOS applies its own
mask, and a rounded source is rounded twice.

An amber-square version was tried and rejected, and the reason is worth keeping because the
obvious measurement is the wrong one. Judged *inside* the icon, amber wins easily: 4.75:1 against
the dark square where the board pair is only 2.99:1. But a favicon is read against a tab strip,
not against itself — and there amber is 1.94:1 on a light strip, so the tile dissolves at its
edges, while the board pair is 3.07:1 and keeps its silhouette. **Measure an icon against what it
sits on, not against itself.**

Note that XML forbids `--` inside a comment, so a CSS token name cannot be *spelled* in
`icon.svg` — librsvg rejects the whole file, and the icon silently disappears.

**`app/manifest.ts` injects its own `<link rel="manifest">`.** Do not add one to `metadata` as
well, or the head carries two.

**The sitemap and the navigation drifted once and will again.** `/tournaments` shipped with
ADR-0043, went into both navs, and never reached `sitemap.ts` — unlisted for the whole life of the
feature, because nothing compared the two lists. Both now read `staticRoutes`, and
`site.test.ts` fails if a nav link is missing from it. A new static route goes in `staticRoutes`
first.

## Traps

**The site must load without Clerk keys.** `src/proxy.ts` called `clerkMiddleware()`
unconditionally, and it throws without a publishable key — a throwing proxy takes every route with
it, so `/`, `/about`, `/leaderboard` and `/play` all answered 500 on a fresh clone. `AuthProvider`
and `AccountBar` both degraded correctly and it made no difference. **A guard downstream of a
throwing proxy is not a guard.**

**An empty environment variable is not an absent one.** `??` does not cover `""`, and an empty
`NEXT_PUBLIC_API_URL` produced a build that fetched from nowhere. `lib/env.ts` (`originFromEnv`)
is the one place that decides, and it trims and falls back on blank.

**`apps/web/public/` must exist even though it is empty.** The web image copies it, Docker fails
a `COPY` whose source is missing, and git drops a directory when its last file goes — so deleting
the unreferenced `create-next-app` SVGs broke the *deploy* while `make check` stayed green and the
dev server never noticed. `.gitkeep` holds it open. Nothing is served from there: the icons are
App Router metadata files in `src/app/`.

**Client-side filtering is the point on `/models`.** It filters the whole catalogue with zero
requests across nine keystrokes — measured, and asserted by the browser suite. Note that not every
URL containing `/models` is an API call: a router prefetch is not a request the page made.

## Testing

Logic in `src/lib` is unit-tested with `vitest` (`make test-web`); coverage is measured *and*
enforced (`make test-web-coverage`, NFR-10). Components are covered by Playwright rather than a jsdom
stack. See [TESTING.md](TESTING.md).

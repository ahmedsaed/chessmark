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

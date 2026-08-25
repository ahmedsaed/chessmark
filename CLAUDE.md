# Chessmark — working guide

LLM agents playing chess against each other and against humans. Part benchmark, part show.

**Read first:** [docs/VISION.md](docs/VISION.md) · [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) ·
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/ROADMAP.md](docs/ROADMAP.md) ·
[docs/adr/](docs/adr/)

The owner directs vision and gives feedback; Claude owns implementation. When something is
ambiguous, ask rather than guess — but do all the unblocked work first.

---

## Commands

Everything runs from the repo root via `make`:

| Command | What |
| --- | --- |
| `make setup` | Install all dependencies, create `.env` |
| `make up` / `make down` | Start / stop Postgres + Redis |
| `make api` | FastAPI with reload on :8010 |
| `make web` | Next.js on :3010 |
| `make check` | **Everything: lint, typecheck, tests.** Run before declaring work done |
| `make psql` / `make redis` | Datastore shells |

Backend commands run under `uv` from `apps/api`; frontend under `pnpm` from `apps/web`.
`uv` lives at `~/.local/bin/uv` — export `PATH="$HOME/.local/bin:$PATH"` if it isn't found.

## Ports — non-standard, deliberately

**3010** web · **8010** api · **5433** postgres · **6380** redis

This machine runs other projects on 3000/8000/5432/6379. Do not "fix" these back to defaults.
See [ADR-0012](docs/adr/0012-nonstandard-local-ports.md).

---

## Non-negotiable invariants

These are the rules that, if broken, quietly ruin the project. Each traces to an ADR.

1. **The server is the only authority on board state.** Models and clients propose; `python-chess`
   validates and disposes. A model must never be able to corrupt a game record.
2. **The message list is append-only and byte-stable.** Never inject a timestamp, move counter, or
   anything else changing into the system prompt or an earlier message. This silently destroys
   prompt caching and multiplies cost. ([ADR-0003](docs/adr/0003-full-transcript-prompt-caching.md))
3. **Log verbatim, never summarised.** Raw request and response payloads, with secrets redacted. If
   a number appears on the leaderboard, its transcript must be one click away.
4. **Cost is computed from actual returned token counts.** Never estimated.
   ([ADR-0011](docs/adr/0011-server-keys-layered-budgets.md))
5. **Ranked games run one fixed, versioned configuration.** No trash talk, no personas, prompt and
   tool-schema versions recorded. Anything else is unranked.
6. **Illegal-move errors return the full legal move list.** Five retries, then
   `illegal_move_forfeit`. ([ADR-0002](docs/adr/0002-illegal-move-policy.md))
7. **Every state change appends exactly one `game_events` row.** Live, reconnect, and replay all
   read that one table. ([ADR-0008](docs/adr/0008-game-events-log.md))
8. **Reasoning is never exposed mid-game.** It would leak the model's plan to its opponent or to a
   human player.
9. **Turn jobs are idempotent via `expected_ply`.** Redelivery must always be safe.
   ([ADR-0007](docs/adr/0007-turn-level-jobs.md))
10. **No API key ever reaches the client.** All provider calls originate in the worker tier.

---

## Layout

```
apps/api/src/chessmark/
  api/        HTTP routes, schemas, SSE endpoints
  agents/     LLM runtime: tools, turn loop, transcript building
  game/       Chess rules, referee, PGN — pure domain, imports nothing else
  db/         SQLAlchemy models, sessions, repositories
  core/       Config, logging, errors
apps/web/src/app/    Next.js App Router
docs/                Vision, requirements, architecture, roadmap, ADRs
```

`game/` must stay dependency-free of `db/`, `agents/`, and `api/`. There's a test enforcing it.

## Conventions

**Python** — 3.12, `ruff` (100 cols), **strict mypy** (all new code fully typed), async throughout
for I/O. Tests in `apps/api/tests/` mirroring the source tree. Markers: `integration` (needs a
database), `llm` (costs real money — never in default CI).

**TypeScript** — Server Components by default; `"use client"` only where interactivity demands it.
Tailwind for styling. `pnpm exec next typegen` must run before `tsc` — Next.js 16 generates the
global route types (`LayoutProps`, `PageProps`) that app code depends on.

**Design** — the system is settled: see [ADR-0013](docs/adr/0013-design-system.md). Tokens live in
`apps/web/src/app/globals.css` as Tailwind `@theme` variables. **No component hard-codes a colour** —
always read a token. Dark only; there is no light theme. Live game layout is stats left, board
centre, conversation right, with finished turns folded and the live turn expanded.

> ⚠️ **Next.js 16 differs from older Next.js.** `apps/web/AGENTS.md` is auto-generated and says so.
> Before writing frontend code, read the relevant guide under
> `apps/web/node_modules/next/dist/docs/`. Do not rely on Next.js knowledge from memory.

**Migrations** — every schema change gets an Alembic migration. `alembic check` must report no drift.

**Commits** — imperative subject, reference phase and requirement IDs where relevant
(e.g. `feat(agents): add illegal-move retry loop (AGENT-05, AGENT-06)`).

## Definition of done

A phase is done when its **exit criteria in [ROADMAP.md](docs/ROADMAP.md) verifiably pass** and its
tests are written. Not "it works on my machine". Deferring a test defers the phase — say so plainly
rather than marking it complete.

---

## Available MCP servers

Configured in `.mcp.json` (project-scoped):

| Server | Use for |
| --- | --- |
| `postgres` | Querying the local dev database directly — inspect schema, check what a game actually wrote |
| `playwright` | Driving the frontend in a real browser: verifying UI, screenshots, E2E from Phase 7 |
| `context7` | Current library docs. Valuable here — Next.js 16, LiteLLM, and Clerk all move fast |

## Testing never calls a provider

**The suite must stay free to run and deterministic.** LLM responses are replayed from cassettes
in `tests/fixtures/llm/`; a missing one raises rather than falling back to a live call. Recording
is deliberate and manual (`make record-llm`), never something CI can trigger. Where a provider
shape cannot be reached on the free tier, hand-author the fixture and mark it `HAND-AUTHORED` —
a test enforces that. Anything that would spend money carries the `llm` marker or lives in
`scripts/`, run by hand.

## Current state

**Phases 0–10, 12, 18, 19, 21 and 22 complete.** 864 backend + 122 frontend tests.

- `chessmark.game` — the chess domain. `ChessBoard`, `Referee`, `IllegalMoveError` (reason,
  human-readable detail, full legal move list), PGN export. 99.75% coverage, pure by enforcement.
- `chessmark.db` — 15 tables, Alembic migrations, async sessions, repositories. `game_events`
  appends are gap-free under concurrency.
- `chessmark.agents` — the LLM gateway and the agent runtime. `LlmGateway` (injectable provider
  call, classified retries), response normalisation, exact `Decimal` costing, credential
  redaction, registry sync, the seven tools, the append-only transcript, and `TurnRunner`.
  96% coverage.

- `chessmark.orchestration` — the queue, the worker, the reconciler, and `human.py`. Redis Streams
  consumer group, `expected_ply` idempotency, one transaction per turn, ack-after-commit. A turn is
  enqueued **only when a model is to play it**: handing the queue a job for a person's move
  produces one the worker can only answer with `awaiting_human`, and it then lingers as a stale
  entry the human's own next job queues behind. That bug cost a full debugging pass.
- `chessmark.api` — REST plus SSE with `Last-Event-ID` reconnect. Reasoning is withheld while a
  game is live (invariant 8).
- `apps/web` — the site shell (header, footer, error and not-found boundaries, site OpenGraph
  card, sitemap and robots), a landing page led by a live game, `/about`, the live game page
  (stats left, board centre, conversation right), and the replay: a finished game is scrubbable
  ply by ply, with the raw provider payloads behind every turn one click away. Replay truncates
  the event log and reuses the live view's fold, so the two cannot drift
  ([ADR-0008](docs/adr/0008-game-events-log.md)). The root layout used to render `{children}` and
  nothing else, so each page hand-rolled a back-link and the account controls existed on one page
  only — Phase 18 fixed that.
- **Auth and spend controls** ([ADR-0006](docs/adr/0006-clerk-for-auth.md),
  [ADR-0011](docs/adr/0011-server-keys-layered-budgets.md)) — Clerk JWTs verified against cached
  JWKS with the algorithm pinned; four independent budget layers (global kill switch, per-user
  daily quota, per-game cap, per-turn ceiling); sliding-window rate limiting; an admin surface.
  Reading stays open to everyone. Exercised against a real Clerk instance: a real sign-in works,
  and JIT user provisioning commits (it silently did not, at first — `/me` answered correctly
  while `users` stayed empty). **The `user.deleted` webhook is still not wired**, so a deleted
  Clerk account leaves its rows behind. That one blocks a public launch, not a deploy.
- **Contestant identity** ([ADR-0015](docs/adr/0015-quantization-as-identity-and-pinned-endpoints.md),
  superseding much of [0014](docs/adr/0014-provider-routing-and-quantization.md)) — a contestant is
  **`(model, quantization)`**, so `model@fp4` and `model@fp8` are ranked separately rather than one
  being banned. Every seat **pins one endpoint** for the whole game, chosen by uptime; the router
  used to switch mid-game, and did. A provider's mangled output abandons the game instead of
  forfeiting the model.

`agents/scripted.py` is the workhorse for testing and for local development: it plugs in as
`LlmGateway(completion_fn=...)` so a whole game can run with no API key, exercising the real path
with only the provider replaced. `make play ARGS="--scripted"` plays a complete game that way.

Database tests need `make up`. Useful targets: `make test-unit` (no database), `make test-llm`
(live provider, opt-in), `make migration m="..."`, `make drift`, `make seed-models`,
`make smoke-llm`.

Paid models work and are cheap. Two benchmark games so far:

| | result | plies | cost | illegal | cache |
| --- | --- | --- | --- | --- | --- |
| gemini-2.5-flash-lite vs deepseek-v4-flash | 1/2-1/2 `ply_cap` | 80 | $0.076 | 4 / 2 | 83% |
| gemini-3.7-flash vs kimi-k2.5 | **1-0 checkmate** | 39 | $0.124 | **0** / 5 | 73% |

The second is the first decisive result, and both sides played nine moves of correct Richter-Rauzer
theory. Across two games, **every** illegal attempt has been `not_reachable` — board-state tracking,
never rule knowledge. Free models cannot finish a game at all: too slow, too verbose, no caching.

**Reasoning must be handed back, not just recorded.** Gemini 3 rejects a function call missing its
`thought_signature`; DeepSeek rejects a thinking-mode history missing `reasoning_content`.
OpenRouter normalises both into `reasoning_details`, which `transcript_messages` now stores and
replays verbatim. LiteLLM files it under `provider_specific_fields`, not at the top of the message.

**An endpoint can break a result without touching precision.** `deepseek-v4-pro` leaked raw DSML
markup instead of tool calls on 9 of 63 calls via StreamLake and 0 of 40 via Baidu and DeepInfra —
same model, same fp8. Provider is recorded per call for exactly this reason
([ADR-0014 amendment](docs/adr/0014-provider-routing-and-quantization.md)).

**The ply cap is a cost bound, not a rules bound** — threefold and the fifty-move rule are applied
automatically, so games terminate on their own. 300 plies is the standard; 80 sat at the median of
real games and let the harness decide half the results.

- `chessmark.bench` — Glicko-2 from Glickman's paper, and the rules for which games may be rated.
  A contestant is `(model, quantization)`. Forfeits count; harness stops do not. Pure, like `game/`.

**Phase 10 — human vs model — is complete.** `orchestration/human.py` and six endpoints; the
worker stops at a human turn and enqueues nothing; the reconciler tells "waiting on a person" apart
from "stalled" and expires idle games after two hours. `/play` has a two-tab chooser with
`NewHumanGame` first, `GET /games/mine` and `MyGames` surface a game as *yours* on `/play` and the
lobby, and the whole flow was driven in a browser against a real Clerk instance and a real
provider — sit down, drag a move, the model replies, reload, resign.

**Human→model chat is off by default here**, opt-in per game, because it is unmoderated: messages
are stored `PENDING` and delivered verbatim. **Phase 11 is in the backlog** — a classifier is
machinery ahead of its need for a site with one user — so the launch condition is now "no
unmoderated channel is reachable", satisfied either by building it or by shipping with conversation
off entirely (see Phase 17's launch conditions).

**A trap for whoever builds moderation:** `moderation_status` is filtered by
`GET /games/{id}/messages`, which the UI does not read. The live conversation is built from
`game_events`, and `_record_said` appends the message content into the event payload unfiltered — so
blocking a message today hides it from nobody. The check has to run *before* the event is appended
and before the opponent's transcript is written, because both are append-only and the transcript is
byte-stable for caching (invariant 2).
The other deliberate limitations stand — no clock, advisory-only human draw offers, promotion
always to a queen.

**The site would not load at all without Clerk keys.** `src/proxy.ts` called `clerkMiddleware()`
unconditionally and it throws without a publishable key; a throwing proxy takes every route with
it, so `/`, `/about`, `/leaderboard` and `/play` all answered 500 on a fresh clone. `AuthProvider`
and `AccountBar` both degraded correctly and it made no difference. The proxy now reads the same
variable they do — worth remembering that a guard downstream of a throwing proxy is not a guard.

**Then: Phase 17a — deploy.** There is still no Dockerfile and no supervised worker anywhere;
`docker-compose.yml` runs Postgres and Redis only.

Frontend logic in `apps/web/src/lib` is unit-tested with `vitest` (`make test-web`); components
are still covered by Playwright rather than a jsdom stack.

**Credits are a granted balance with a history** (ADR-0016, AUTH-10..14). `users.credit_balance`
stays the enforcement point — a charge must be one statement whose `WHERE` clause is the check —
and `credit_ledger` is the append-only account of how it got there. The two are asserted to agree,
including for balances that predate the ledger, which the migration gives an opening entry.

**Identity is resolved from Clerk at provisioning**, because a session token carries no email by
default and every row used to read `NULL`. `core/clerk.py` asks the API once, on genuine first
sight; `make backfill-identities` fills rows created before that. An admin grants by email, Clerk
id, or ours — and an address we do not hold is looked up with Clerk, so credits can be granted to
someone who has not signed in yet.

**Three kinds of model are never registered** (AGENT-14), all for one reason — every one of these
failures is a *forfeit*, recording a loss against a model that never had a chance. No tool calling;
`:batch` variants, which are asynchronous; and a context window under `MIN_CONTEXT_TOKENS`
(128k). The last is derived: the transcript grows **1,818 tokens per ply**, measured, so 128k
covers ~70 plies. It removed 14 models, including `gpt-3.5-turbo-0613` at 4,095 tokens.

Known gaps, recorded rather than quietly carried: Phase 7's Lighthouse score is **unverified**
(no Lighthouse in this environment), NFR-06's >80% cache rate is met in aggregate but not by
Gemini individually, Phase 8's PGN is verified against `chess.js` but **not against Lichess or
SCID themselves**, and there is **no automated browser suite** — every UI claim in
Phases 7, 8, 10, 18 and 19 was checked by hand, so no test would catch a layout regression.

Clerk's `user.deleted` **is** handled (`api/routes/webhooks.py`), contrary to what this file said
for a while; what is missing is a test over the route handler and registration of the endpoint in
the Clerk dashboard. Only signature verification is covered.

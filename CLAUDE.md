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

## Tournaments (Phase 13)

A tournament is a **format, a field, and a set of bounds**. The field is a `FieldFilter`, never a
list, so every bracket is the same machinery: `--free`, `--open-weights`, `--provider anthropic`,
`--max-credits 1` all resolve through one query.

```
make tournament ARGS="field --free"                 # who would enter; costs nothing
make tournament ARGS="create --name '…' --slug x --free --format pool"   # never ends
make tournament ARGS="create --name '…' --slug y --free --format swiss --rounds 5"
make worker                                          # exactly one; run schedules, it does not play
make tournament ARGS="run x"                         # ticks until finished or paused
make tournament ARGS="pause x --abort-live"          # stop; --abort-live frees queued jobs
make tournament ARGS="resume x --max-usd 5"          # raise the ceiling that stopped it
make tournament ARGS="withdraw x vendor/model:free"
make tournament ARGS="standings x"
```

**A pool is the open case.** `--format pool` never ends, re-resolves its field every tick so a
newly listed model joins by itself, and ranks by Glicko-2 rather than points — which is what makes
an open population rankable at all. Its matchmaker follows what the rating actually measures: the
least-known entrant plays first, against the nearest-rated opponent who is not a rematch. It pairs
only what it can run, because scheduling ahead would freeze that information at the moment it was
written, and a pool never runs out of fixtures.

Pools are **ranked** (an unranked one would play forever and measure nothing) and a pool over paid
models is **refused without `--max-usd`** — with no end, the ceiling is the only thing that ever
stops it. Raise it with `resume --max-usd`; nothing resets on its own.

A model that leaves the catalogue is **not** auto-withdrawn from a pool. Its games are real results
and its rating is real; dropping it because an endpoint went quiet for an afternoon would rewrite
history.

**A closed event's field is frozen when it is created.** `resolve_field` runs once and writes
`tournament_entrants`; a model registered afterwards does not join, and one that disappears
upstream does not leave. That is deliberate — a round robin schedules its whole fixture list from
the field, so admitting a latecomer would invalidate it, and a table whose rows played different
opponents means different things per row. To change a running field, `withdraw` an entrant or
create a second event.

A model that becomes unplayable mid-event is **not** handled gracefully by itself: its games are
attempted, fail at ply 0, and are marked *abandoned* — honest, but it wastes pairings. `withdraw`
is the deliberate path, and it abandons the unplayed pairings rather than awarding them, because a
walkover is not a finding about the opponent.

`advance` holds no state between calls, which is the whole of the resume criterion: a restart asks
the table what has been played rather than trusting a dead process. A **paused** event returns
early from `advance` — without that it restarts on the next tick, including one its own budget
stopped, which would then spend past the ceiling it had just halted at.

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

## The browser suite

Playwright, in `apps/web/e2e/`. Two projects, because the flows differ in what they need:

| | runs | needs |
| --- | --- | --- |
| `public` | `make test-e2e` — **and CI** | a running stack, nothing else |
| `signed-in` | `make test-e2e-all` | a real Clerk development instance |

Reading is open to everyone (AUTH-02), so the lobby, the catalogue, a model page and a whole
replay assert with no identity at all. The signed-in flows sign in **for real** — a genuine Clerk
session JWT, verified against real JWKS — using a `+clerk_test@example.com` address, which a
development instance treats as a test identity: no mail is sent, the code is fixed, and **no
password lives in this repository**. They are opt-in and skipped rather than faked when the keys
are absent, the same bargain the `llm` marker strikes.

The suite starts its own **scripted worker** and seeds its own fixtures, so it spends nothing:

- `agents.scripted.responsive` is a scripted opponent that **reads the board** — it asks for the
  legal moves and plays the alphabetically first, deterministically. Every other helper in that
  module replays a fixed list, which cannot answer a person.
- `scripts/worker.py --scripted` runs the real worker with only the provider replaced. Its log is
  `apps/web/e2e/.auth/worker.log` — **the first place to look when the board never moves.**
- `scripts/seed_e2e.py` plays a whole game (Scholar's Mate) through the real queue and worker so
  replay has something finished to scrub. Idempotent, and it seeds a minimal catalogue only when
  the registry is empty, which is CI — a developer's 256 real models are left alone. It runs in
  `global-setup.ts` **before** the worker is started, and that order is load-bearing (see below).
- `scripts/seed_e2e_user.py` creates and funds the test account. New users get no credits by
  design (AUTH-11), so an unattended suite would otherwise be unable to start a game.

**The suite tests a *running* stack, so a stale process gives a stale answer.** A leak that the
browser reported as real turned out to be an API server started hours earlier, serving events
without the redaction the worker beside it was already writing. Restart the API and the worker
after backend changes, or run them with reload.

**Only one worker may consume the queue.** A job goes to whichever worker reaches it first, so a
second one is not redundancy — it is a coin toss over who plays each turn. Seeding used to run as
a Playwright project, i.e. *after* `global-setup.ts` had started the background worker, and the two
then fought over the seeded game: the seed plays a fixed script while the worker plays whatever
`responsive` decides. Locally the seed usually won and the game came out at its expected seven
plies; in CI it lost and the game ran to fifteen. If you are running `make worker` by hand, stop it
before running the suite.

**CI serves a production build; `make web` serves `next dev`.** They are not the same target. A
production build prefetches its own routes over RSC, so `localhost:3010/models?_rsc=…` appears in
the network log there and never under `next dev` — which is why an assertion about "no request per
keystroke" passed locally and failed in CI. To reproduce CI exactly:

```
cd apps/web && pnpm build && pnpm start     # not pnpm dev — and on 3010, not another port
pnpm exec playwright test --project=public
```

**On 3010 specifically.** The API allows one CORS origin (`cors_origins`, default
`http://localhost:3010`), so serving the front end anywhere else makes every browser-side fetch
fail and the suite reports it as missing UI. That is the suite working — it catches a CORS
misconfiguration, which is worth knowing before Phase 17a puts this behind a real domain.

Four traps, each of which cost a debugging pass:

1. **A message's content is not always a string.** By the time it reaches the provider, the
   prompt-caching path may have wrapped it into `[{"type": "text", ...}]` so a `cache_control`
   marker can ride along (ADR-0003). Parsing it as a string made the scripted opponent ask for
   the legal moves twenty times in one turn, in silence.
2. **`/ w /` is not "the model has replied".** The starting position is white-to-move too, so the
   wait was already satisfied and every later assertion read a board that had not moved.
3. **The first `aria-expanded="false"` on a signed-in page is the account button**, not a turn.
   Clicking it opens the Clerk user menu over the page and every later click fails on an element
   it has covered. Scope fold selectors by their text.
4. **Not every URL containing `/models` is an API call.** Assert against the API origin, or a
   router prefetch counts as a request the page did not make.

Frontend `lib/` coverage is measured *and* enforced (`make test-web-coverage`, NFR-10).
`api.ts` and `site.ts` are excluded from that floor and covered by the browser suite instead:
unit-testing fetch wrappers means mocking `fetch` and then asserting the mock.

## Current state

**Phases 0–10, 12, 13, 18–23 complete.** 993 backend + 122 frontend + 18 browser tests.

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
  game is live (invariant 8), by `api/redaction.py` — **on the way out, not when the event is
  written**. Two models cannot read each other's stream, so an audience watching both sides think
  leaks nothing (ADR-0013); a person holding a seat is a participant, so their live game's
  `thinking` and `output` payloads are stripped of text and keep only the token count. Both read
  paths must apply it — the REST event log *and* the SSE stream — and there is a test per path,
  each verified to fail when its own gate is removed.

  This used to be enforced in `agents/turn.py`, which simply never wrote the text for a game with
  a human seat. The log is append-only (ADR-0008), so that was permanent: a person's own games
  were the only ones whose reasoning the transcript could never show, long after there was
  anything left to leak. Model prose travels the same route, because Gemini says everything in
  `content` and nothing in `reasoning`.
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
never rule knowledge. Free models were long recorded as unable to finish a game at all; that
turned out to be an endpoint bug rather than the models — see below.

**Reasoning must be handed back, not just recorded.** Gemini 3 rejects a function call missing its
`thought_signature`; DeepSeek rejects a thinking-mode history missing `reasoning_content`.
OpenRouter normalises both into `reasoning_details`, which `transcript_messages` now stores and
replays verbatim. LiteLLM files it under `provider_specific_fields`, not at the top of the message.

**An endpoint can break a result without touching precision.** `deepseek-v4-pro` leaked raw DSML
markup instead of tool calls on 9 of 63 calls via StreamLake and 0 of 40 via Baidu and DeepInfra —
same model, same fp8. Provider is recorded per call for exactly this reason
([ADR-0014 amendment](docs/adr/0014-provider-routing-and-quantization.md)).

**Free models can play — the note that said otherwise was measuring a bug.** `fetch_endpoints`
stripped the `:free` suffix before asking OpenRouter which providers serve a model, on the belief
that the endpoints route did not accept it. It does. A free variant is served by an entirely
different — usually *single* — provider from the paid one, so the paid variant's 29 providers were
stored against the free slug, the seat pinned the highest-uptime one (ADR-0015), and every free
game died at ply 0 with a 404 naming a provider we had never chosen. Two tests pin the path now,
both verified to fail when the strip returns.

With that fixed, `poolside/laguna-s-2.1:free` vs `nvidia/nemotron-3.5-lightning:free` played 22
plies of the Giuoco Piano — five moves of correct theory, both sides castled, a real bishop
sacrifice on f7 — with **zero illegal attempts**. What remains true is that they are slow and
verbose: 17s and 38s mean latency against 2.6s for paid models, worst call 442s, ~1,100–1,950
output tokens per call, and 2.95 calls per ply.

**The free tier is a shared pool, and it is patchy.** 429s carry
`limit_source: upstream_provider_shared_pool` and arrive from first-party providers too — a probe
of six free models returned four 200s, one 429 and one 403. Our `RetryPolicy` backs off a maximum
of **8 seconds** and reads nothing from the provider's `Retry-After`, so a hot provider costs ~20
doomed requests and then abandons the game. A tournament has no deadline and should wait minutes;
fixing that is a prerequisite for Phase 13, not an optimisation.

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

**The catalogue has pages** (Phase 20). `/models` filters 275 models client-side — measured at
zero requests across nine keystrokes — and `/models/[slug]` prints what a model has actually done
over *every* game, which is deliberately not the leaderboard's number: that one covers ranked games
only, keyed by contestant, because a rating may see nothing else (BENCH-03). Money and tokens come
from `llm_calls` so a page cannot disagree with the call log; results come from `players`. A model
that played itself is one game and two seats.

**Four kinds of model are never registered** (AGENT-14). Three cannot finish a game, and each
failure is a *forfeit* — a loss against a model that never had a chance: no tool calling; `:batch`
variants, which are asynchronous; and a context window under `MIN_CONTEXT_TOKENS` (128k, derived —
the transcript grows **1,818 tokens per ply**, measured, so 128k covers ~70 plies). The fourth is
different: a **floating alias** (`-latest`, `~vendor/…`) plays fine but cannot say what played, so
its record is unreproducible (BENCH-04). ADR-0015 originally kept these playable-but-unrankable and
was [amended](docs/adr/0015-quantization-as-identity-and-pinned-endpoints.md) once it was clear that
a game record which cannot name its weights is as useless as a rating across them.

`GET /models` returns only models with an active tool-capable endpoint. It listed everything
registered for about an hour, which meant the catalogue page advertised 18 models the picker
correctly refused — `playable=false` still reaches the registry as stored.

Known gaps, recorded rather than quietly carried: Phase 7's Lighthouse score is **unverified**
(no Lighthouse in this environment), NFR-06's >80% cache rate is met in aggregate but not by
Gemini individually, Phase 8's PGN is verified against `chess.js` but **not against Lichess or
SCID themselves**, and the browser suite's **signed-in half does not run in CI** — it needs a real
Clerk instance, so CI asserts the public pages only and the playing flow is asserted locally by
`make test-e2e-all`.

Clerk's `user.deleted` **is** handled (`api/routes/webhooks.py`), contrary to what this file said
for a while; what is missing is a test over the route handler and registration of the endpoint in
the Clerk dashboard. Only signature verification is covered.

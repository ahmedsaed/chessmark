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

## `./chessmark` — the stack without a toolchain

`make` is for a development machine. On a server, where Docker is all there is, `./chessmark` runs
everything inside a container: no uv, no node, and no remembering which compose files to combine.
`./chessmark help` lists the commands and `./chessmark help <command>` gives examples.

`credits` grants or revokes by email, Clerk id, or ours, and `--show` prints a balance with the
ledger behind it. The resolver is `db.users.resolve_user`, shared with `POST /admin/credits` — an
email we do not hold is asked of Clerk, so an invitation can be pre-funded for somebody who has
never signed in. `core.clerk.get_directory` moved out of `api/deps` for that: `db/` importing
`api/` would invert the layering for one cached client.

`deploy` is the whole sequence — pull, migrate, restart, check `/ready`. `restart` uses
`--force-recreate` on purpose: a container that once failed to bind its port can come back
running-but-unpublished, healthy inside and unreachable outside, with `docker port` empty. `sql`
runs with `ON_ERROR_STOP=1`, and both it and `backup` read the credentials from inside the
container, so there is no second copy of `POSTGRES_USER` to drift.

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

**Only one worker may consume the queue *here*.** Not because two is unsafe — the queue is a Redis
Streams consumer group, so a job goes to exactly one consumer and identical workers share the load
(see `WORKER_REPLICAS`). The rule is about workers that are *not* identical: a scripted one racing
a real one is a coin toss over who plays each turn, with different providers. Seeding used to run as
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

**Reasoning, output and tool calls each have their own disclosure**, and output is closed by
default. One fold per turn meant reading a tool call also unrolled several thousand words of
reasoning, so the thing you wanted was pushed off screen by the thing you did not. Each trigger
carries a size hint (`reasoning · 2.4k`) because the question a reader is asking is whether it is a
glance or a scroll. Illegal attempts unroll with the tools — an illegal move *is* a failed
`make_move` — and `raw` belongs to the turn, so it is reachable while everything is folded.

**The right-hand column is `EventStream`, not `Conversation`.** It was named for trash talk and
had long since stopped being that: it carries reasoning, output, tool calls, illegal attempts, and
now the harness interrupting itself. A pause renders as a **notice** — full width, no side — because
a rate limit is not something either contestant did, and drawing it as a player's message would
attribute the harness's failure to a model. Notices belong to no turn (the failed turn is rolled
back whole, so its `turn_started` never reaches the log) and are interleaved with the turns by
`seq`.

Frontend `lib/` coverage is measured *and* enforced (`make test-web-coverage`, NFR-10).
`api.ts` and `site.ts` are excluded from that floor and covered by the browser suite instead:
unit-testing fetch wrappers means mocking `fetch` and then asserting the mock.

## Current state

**Phases 0–10, 12, 13, 17a, 18–23 complete.** 1052 backend + 133 frontend + 18 browser tests.

- `chessmark.game` — the chess domain. `ChessBoard`, `Referee`, `IllegalMoveError` (reason,
  human-readable detail, full legal move list), PGN export. 99.75% coverage, pure by enforcement.
- `chessmark.db` — 15 tables, Alembic migrations, async sessions, repositories. `game_events`
  appends are gap-free under concurrency.
- `chessmark.agents` — the LLM gateway and the agent runtime. `LlmGateway` (injectable provider
  call, classified retries), response normalisation, exact `Decimal` costing, credential
  redaction, registry sync, the seven tools, the append-only transcript, and `TurnRunner`.
  96% coverage. Every call of one game carries **one OpenRouter `session_id`** — `game-<uuid>`,
  derived and never stored — so a whole match is one grouped conversation on the provider's own
  dashboard (LOG-08). It rides in `extra_body` beside `usage` and `provider`, because all three
  are top-level OpenRouter body fields that LiteLLM does not know by name and would otherwise
  drop. It is also OpenRouter's **sticky routing key**, which is why `agents/sessions.py` argues
  at length for both seats sharing one id
  ([ADR-0015 amendment](docs/adr/0015-quantization-as-identity-and-pinned-endpoints.md)).

**`game_events.created_at` is the *transaction* timestamp.** It defaults to `now()`, which in
Postgres is constant for a transaction, and a turn commits everything it produced in one (NFR-08) —
so a turn's `turn_started` and its `move_made` carry the identical instant. Any timing derived from
the log *within* a turn measures nothing, and "move landed → next turn started" reads back the
previous turn's duration, plausibly enough to be believed. The reliable clocks are `latency_ms` on
`turns` and `llm_calls`, both `perf_counter` in-process. `./chessmark latency <game-id>` does the
decomposition properly: provider, harness, and the queue wait derived by subtraction.

- `chessmark.orchestration` — the queue, the worker, the reconciler, and `human.py`. Redis Streams
  consumer group, `expected_ply` idempotency, one transaction per turn, ack-after-commit.
  **A worker plays one turn at a time, start to finish**, so a free model's turn — 17–38s typical,
  442s worst — blocks whatever is behind it. `WORKER_REPLICAS` (`./chessmark workers 3`) runs more:
  the consumer group shards jobs, so they share rather than duplicate. Every worker also runs a
  reconciler, and those *would* duplicate — two seeing the same free concurrency slot and each
  filling it — so the sweep takes a Redis `SingleFlight` lock with a TTL. A TTL rather than a real
  lock, because a missed sweep costs a minute and a lock nobody can release costs everything after
  it. A turn is
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
of six free models returned four 200s, one 429 and one 403.

**A rate limit pauses the game; it does not retry it** ([ADR-0017](docs/adr/0017-rate-limits-pause-games.md),
OPS-12..14). This cost a production incident worth remembering in full, because three of its four
causes do not look like the problem:

- The gateway made **8 attempts** and the worker requeued **5 times with no delay** — 40 requests
  per game, then *abandoned*. ~560 doomed requests in ninety minutes, all charged against the
  allowance the retries were protecting. The instinct to make a rate limit *more* patient is
  backwards: patience inside the retry loop is paid for in requests.
- The delay ladder was decided by `base_delay = 0.5s`, so eight doublings reached 32 seconds and
  `rate_limit_max_delay = 300` never bound. Rate limits have their own base now.
- **A provider 404 is unavailability, not a bad request.** It was classified with 400 and 422 as
  "the request itself is unacceptable", and a game between two free models reached **ply 55 and
  1.17M tokens** before being abandoned outright on `{"code":404,"provider_name":"Nvidia"}`. The
  model was still listed, the endpoint still there at 97.8% uptime, and a fresh call answered — a
  blip, and a good game thrown away for it. Both 429 and provider-404 now pause and cool the
  endpoint down; `RateLimit.describe` words the reason so a reader is not told they were
  rate-limited when they were not.
- `Retry-After` is honoured **when it arrives, which is usually not**: OpenRouter sends one only
  "when every attempted provider returned a retry hint", and a free model is served by exactly one
  endpoint — so the cooldown ladder in `core/cooldown.py` has an opinion of its own.
- **The pool then re-paired the same dead model fourteen times.** An abandoned game is excluded
  from ratings, so the failing entrant's deviation never moved, so it stayed the least-known
  entrant the matchmaker most wants to play — and `Form.games` was never populated, so the
  tie-break fell to the *alphabetical key*. A deterministic loop. `matchmake(unavailable=...)` is
  the half that breaks it, and it is the fix that mattered most.

So: `GameStatus.PAUSED` with `resume_after`, one `game_paused` event, **no concurrency slot held**
(`in_flight` counts `PENDING` and `RUNNING`), the reconciler resuming it, and a Redis cooldown per
(model, endpoint) that the tournament matchmaker reads. Bounded by a **24-hour span** measured
from the first pause, not by a pause count: six pauses came to ~3½ hours and four real games were
abandoned in one afternoon because two free pools stayed hot for longer than that. A count also
tied the patience to the cooldown ladder's tuning. A game with a human seat gives up after ten
minutes, because a person will not wait out a shared pool.

**Two corrections to that, both from watching it run.** A *ceiling* on paused games — a pool holding
once `max_concurrent + 2` were paused — stalled the pool completely and was reverted: the failure it
guarded against is already prevented by the cooldown, which stops the matchmaker pairing a resting
provider at all, so the pool holds when there is no game worth starting rather than when a number
says so. And **resuming has to ask for the concurrency slot back**: `reconcile` is the one path that
creates running games without going through `_start_games`, so it was the one path where
`max_concurrent` went unchecked, and three games coming due at once would have played in parallel in
a pool bounded to one.

**A harness ceiling is not a finding about a player** (AGENT-17). `FORFEIT_TERMINATIONS` held two
that were, and one pool made it plain: **five of twelve completed games carried a verdict neither
model had earned.**

- **`TIMEOUT` measured the provider.** The same model on two endpoints got two verdicts — the
  routing lottery ADR-0015 exists to remove, reappearing as a clock. One model lost a game **at ply
  1 having never been served a single completion**. A turn that runs out of clock is now *failed and
  retried*, and `TIMEOUT` is resumable, which it was not.
- **`BUDGET_EXCEEDED` counted the prompt**, and the prompt is re-sent every round-trip (ADR-0003) —
  so it measured transcript size times round-trips. A model that produced **5,263** tokens was
  forfeited for "using 514,446": four replays of a 128k transcript. It punished long games hardest
  and big-context models most (a 1M-window model at a 128k prompt is nowhere near compaction's
  trigger, and four round-trips still crossed a flat 400k). It counts **completion tokens** now.

Latency and size are still measured and published — they are statistics. A forfeit says "it played
worse", and of a slow provider that claim is false.

**A gated model is disabled, not paired again** (AGENT-18). `thinkingmachines/inkling-small:free`
answers 403 *"only available on agentic harnesses"* — a distribution allow-list, not a capability
check: we *are* one, and app-attribution headers change nothing. **Nothing in the catalogue predicts
it**: it advertises `tools`, a 1M window, `status: 0` and 100% uptime, identical to a model that
works, and `/api/v1/apps` is an HTML page rather than an API. So it is learned from the refusal —
and 403 joins 429 and provider-404 as unavailability, because a generic error taught the matchmaker
nothing and one pool spent **22 pairings** dying at ply 0 against that one model.

**The pool must not pair a model that already has a paused game.** The cooldown alone left a gap:
its first rung is sixty seconds, so it lapses, the matchmaker sees the model as available, pairs it,
and it is refused again — four paused games against one model with nothing running. Asking about
paused games is the precise fix; a ceiling on paused games was the imprecise one, and was reverted.

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
The other deliberate limitations stand — no clock, advisory-only human draw offers.

**Promotion is chosen, not assumed.** It used to be a queen either way, which is right almost every
time and wrong in exactly the position that matters: the one where a rook or a knight wins and the
player cannot say so. A human drag to the last rank opens a picker; a model that names no piece gets
`MISSING_PROMOTION` — its own reason, because every other explanation was false. `not_reachable`
told a model that had found the move that its pawn could not make it, and charged an illegal attempt
for the privilege.

**A withheld reasoning trace is not an absent one.** `api/redaction.py` strips the text from a game
its reader is playing and keeps the token count (invariant 8), and the panel rendered that
identically to a model that had said nothing — a turn showing only its tool calls, with no hint that
anything was held back. `withheldReasoning` carries the count, and the turn says `thinking · 801`
without saying what about.

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
variants, which are asynchronous; and a context window under `settings.min_context_tokens` (128k,
derived — the transcript grows **1,818 tokens per ply**, measured, so 128k covers ~70 plies). The
fourth is different: a **floating alias** (`-latest`, `~vendor/…`) plays fine but cannot say what
played, so its record is unreproducible (BENCH-04). ADR-0015 originally kept these
playable-but-unrankable and was
[amended](docs/adr/0015-quantization-as-identity-and-pinned-endpoints.md) once it was clear that
a game record which cannot name its weights is as useless as a rating across them.

**"Never registered" was aspirational, and is now enforced.** Three things had gone wrong, all
found through one abandoned game:

1. The floor arrived as `min_context: int = 0`, and `fits_a_game` reads 0 as *admit everything* —
   so a caller who simply forgot the argument opted out of the rule. `refresh_catalogue.py` passed
   it; `seed_models.py` did not. `context_floor(None)` now resolves to `settings.min_context_tokens`
   (128k), so **omitting it applies the policy** and `0` is an opt-out somebody has to type.
2. **A sync re-enabled what a sync had disabled.** `to_registry_entry` stamps `enabled: True` and
   the upsert wrote it onto existing rows, so `refresh-catalogue` disabled a sub-floor model and
   the next `seed-models` brought it back — and an administrator's deliberate disable did not
   survive either. `enabled` is set on **creation only** now.
3. **The endpoint's own window was never read.** A model advertises a context length and an
   *endpoint* serves one; the 400 that abandoned the game said "**this endpoint's** maximum context
   length is 65536". Both numbers were already stored. `endpoint_is_playable()` is now the single
   predicate shared by `select_endpoint`, `GET /models` and `resolve_field`, so the catalogue, the
   field and the picker cannot disagree.

`make prune-registry` applies the rule to the registry as it already stands — reports by default,
`ARGS=--apply` to act. It **disables, never deletes**: `players.model_id` is `ON DELETE RESTRICT`,
so a row with games cannot be deleted at all, and a game must stay readable however its model
turned out. It does remove those models' games, which is the destructive half and the reason it
reports first.

**The floor is 64k, not 128k, because compaction removed the reason for the higher number**
([ADR-0018](docs/adr/0018-context-compaction.md), AGENT-15). The transcript grows ~1,818 tokens per
ply, so 128k bought about seventy plies of a possible three hundred and then *forfeited* —
`context_exceeded` is in `FORFEIT_TERMINATIONS`, so filling the window was recorded as the model
playing badly. No floor fixes that; ~545k would be needed and would exclude the catalogue.

So at `context - prompt < max(reserve, 10%)` the model **summarises its own earlier turns** and
plays on. Chess makes this unusually safe: the server owns the board (invariant 1) and
`get_board_state` is always there, so a lossy summary cannot corrupt play — which is not true of an
agent whose context *is* its world.

Five things about it that are easy to get wrong:

- **The cut lands on a turn boundary**, not a message boundary. A turn is 3–5 messages and every
  provider rejects a `tool` result whose `tool_calls` parent is missing.
- **Nothing is deleted.** Folded rows keep their place in `transcript_messages` with a
  `superseded_at`; `build_messages` stops sending them. The record stays verbatim (invariant 3) and
  only the request shrinks — `full_history()` exists to demonstrate that rather than assert it.
- **`seq` is append-only, so a summary written at ply 60 has the highest sequence number** and would
  replay *after* the turns it summarises. Ordering is explicit: system prompt, then the live
  summary, then the retained turns.
- **The summarising call needs its own small cap.** Sending the near-full history with the usual
  64,000-token `max_tokens` is refused for exactly the reason compaction exists.
- **This is the one named exception to invariant 2.** It costs a cache miss per compaction, which is
  why it cuts deep and happens at most once per turn.

`max_completion_tokens` is now **clamped to the endpoint's window** (AGENT-16). Unclamped it was a
flat 64,000 reconciled against nothing, which asked a 65,536-token endpoint for 65,810 tokens and
was refused — the 400 that abandoned a game at ply 10, and a failure a 64k floor would have
recreated for every model in the new band.

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

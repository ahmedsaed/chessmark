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

**Phases 0–8 complete.** 460 backend + 19 frontend tests, CI green.

- `chessmark.game` — the chess domain. `ChessBoard`, `Referee`, `IllegalMoveError` (reason,
  human-readable detail, full legal move list), PGN export. 99.75% coverage, pure by enforcement.
- `chessmark.db` — 15 tables, Alembic migrations, async sessions, repositories. `game_events`
  appends are gap-free under concurrency.
- `chessmark.agents` — the LLM gateway and the agent runtime. `LlmGateway` (injectable provider
  call, classified retries), response normalisation, exact `Decimal` costing, credential
  redaction, registry sync, the seven tools, the append-only transcript, and `TurnRunner`.
  96% coverage.

- `chessmark.orchestration` — the queue, the worker, and the reconciler. Redis Streams consumer
  group, `expected_ply` idempotency, one transaction per turn, ack-after-commit.
- `chessmark.api` — REST plus SSE with `Last-Event-ID` reconnect. Reasoning is withheld while a
  game is live (invariant 8).
- `apps/web` — the lobby, the live game page (stats left, board centre, conversation right), and
  the replay: a finished game is scrubbable ply by ply, with the raw provider payloads behind every
  turn one click away. Replay truncates the event log and reuses the live view's fold, so the two
  cannot drift ([ADR-0008](docs/adr/0008-game-events-log.md)).
- **Provider routing** ([ADR-0014](docs/adr/0014-provider-routing-and-quantization.md)) — games
  refuse sub-8-bit and undeclared endpoints, so a leaderboard row means one thing. Closed-weight
  models are widened to their own vendor only. The precision that served each seat is recorded.

`agents/scripted.py` is the workhorse for testing and for local development: it plugs in as
`LlmGateway(completion_fn=...)` so a whole game can run with no API key, exercising the real path
with only the provider replaced. `make play ARGS="--scripted"` plays a complete game that way.

Database tests need `make up`. Useful targets: `make test-unit` (no database), `make test-llm`
(live provider, opt-in), `make migration m="..."`, `make drift`, `make seed-models`,
`make smoke-llm`.

Paid models work and are cheap: an 80-ply `gemini-2.5-flash-lite` vs `deepseek-v4-flash` game cost
**$0.076** at an 83% cache hit rate. Free models cannot finish a game — too slow, too verbose, and
no prompt caching.

**Next up: Phase 9 — auth, quotas & cost control.** The hard gate before anything is public.
See [ROADMAP.md](docs/ROADMAP.md#phase-9--auth-quotas--cost-control).

Frontend logic in `apps/web/src/lib` is unit-tested with `vitest` (`make test-web`); components
are still covered by Playwright rather than a jsdom stack.

Three known gaps, all recorded in the roadmap rather than quietly carried: Phase 7's Lighthouse
score is **unverified** (no Lighthouse in this environment), NFR-06's >80% cache rate is met in
aggregate but not by Gemini individually, and Phase 8's PGN is verified against `chess.js` but
**not against Lichess or SCID themselves**.

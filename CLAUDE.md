# Chessmark — working guide

LLM agents playing chess against each other and against humans. Part benchmark, part show.

The owner directs vision and gives feedback; Claude owns implementation. When something is
ambiguous, **ask rather than guess — but do all the unblocked work first.**

## Where things are written down

This file is the rules. Everything else lives in a document that owns it, and **that document is
where a change belongs** — not here.

| | |
| --- | --- |
| [VISION.md](docs/VISION.md) | what this is for, and what it refuses to be |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | numbered requirements — cite the IDs in commits |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | system shape, the turn loop, tool surface, data model |
| [ROADMAP.md](docs/ROADMAP.md) | phases, exit criteria, **and the known gaps** |
| [adr/](docs/adr/) | every decision, why it was made, and what we live with |
| [TESTING.md](docs/TESTING.md) | the three suites and the rules they hold to |
| [TOURNAMENTS.md](docs/TOURNAMENTS.md) | formats, fields, pools, settling |
| [PROVIDERS.md](docs/PROVIDERS.md) | OpenRouter reality, the catalogue, the free tier |
| [FRONTEND.md](docs/FRONTEND.md) | Next.js 16, the design system, `EventStream` |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | containers, `./chessmark`, CD, backups |

**An ADR is immutable.** A decision that changes gets a new ADR that supersedes or amends the old
one, rather than an edit.

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

Database tests need `make up`. Also useful: `make test-unit` (no database), `make test-llm` (live
provider, opt-in), `make migration m="..."`, `make drift`, `make seed-models`, `make smoke-llm`,
`make play ARGS="--scripted"`, `make prune-registry`, `make backfill-identities`.

On a server there is no toolchain — use `./chessmark` ([DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## Ports — non-standard, deliberately

**3010** web · **8010** api · **5433** postgres · **6380** redis

This machine runs other projects on 3000/8000/5432/6379. Do not "fix" these back to defaults.
See [ADR-0012](docs/adr/0012-nonstandard-local-ports.md).

---

## Non-negotiable invariants

These are the rules that, if broken, quietly ruin the project. Each traces to an ADR.

1. **The server is the only authority on board state.** Models and clients propose; `python-chess`
   validates and disposes. A model must never be able to corrupt a game record. This extends to
   anything derived: where a stored verdict and the game record disagree, the game wins.
2. **The message list is append-only and byte-stable.** Never inject a timestamp, move counter, or
   anything else changing into the system prompt or an earlier message. This silently destroys
   prompt caching and multiplies cost. ([ADR-0003](docs/adr/0003-full-transcript-prompt-caching.md);
   compaction is the one named exception, [ADR-0018](docs/adr/0018-context-compaction.md))
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
   human player. Enforced in `api/redaction.py` **on the way out**, not when the event is written —
   both read paths (REST log and SSE) apply it, and there is a test per path.
9. **Turn jobs are idempotent via `expected_ply`.** Redelivery must always be safe.
   ([ADR-0007](docs/adr/0007-turn-level-jobs.md))
10. **No API key ever reaches the client.** All provider calls originate in the worker tier.
11. **A harness bound is never a finding about a player.** Our ceilings fail a turn; they do not
    forfeit a model. ([ADR-0019](docs/adr/0019-harness-bounds-are-not-findings.md))

---

## Layout

```
apps/api/src/chessmark/
  api/            HTTP routes, schemas, SSE endpoints, redaction
  agents/         LLM runtime: gateway, tools, turn loop, transcript, compaction
  game/           Chess rules, referee, PGN — pure domain, imports nothing else
  orchestration/  Queue, worker, reconciler, tournaments, human seats
  bench/          Glicko-2 and the rules for which games may be rated — also pure
  db/             SQLAlchemy models, sessions, repositories
  core/           Config, logging, errors, cooldown
apps/web/src/     Next.js App Router, components, lib
docs/             Vision, requirements, architecture, roadmap, ADRs, guides
scripts/          Anything run by hand, including anything that spends money
```

`game/` and `bench/` must stay dependency-free of `db/`, `agents/` and `api/`. There is a test
enforcing it.

---

## Conventions

**Python** — 3.12, `ruff` (100 cols), **strict mypy** (all new code fully typed), async throughout
for I/O. Tests in `apps/api/tests/` mirroring the source tree.

**TypeScript** — see [FRONTEND.md](docs/FRONTEND.md). Read the Next.js 16 guides in
`node_modules/next/dist/docs/` rather than relying on memory.

**Migrations** — every schema change gets an Alembic migration. `alembic check` must report no
drift (`make drift`).

**Commits** — imperative subject, reference phase and requirement IDs where relevant
(e.g. `feat(agents): add illegal-move retry loop (AGENT-05, AGENT-06)`).

**Comments explain *why*, and name the failure they prevent.** The codebase is dense with these on
purpose: a rule with its reason attached survives a refactor, and one without it gets tidied away by
somebody who could not see what it was for. A comment that only restates the code is noise — delete
it.

## How to work here

- **A fix needs a test that fails without it.** Verify that, don't assume it. More than one bug in
  this repository was found because an assertion that could never fail was noticed.
- **Write the reason down where it belongs.** A decision goes in an ADR, a trap goes next to the
  code or in the guide that owns it, a gap goes in ROADMAP's *Known gaps*. Not here.
- **Prefer the narrow fix.** A ceiling on paused games was reverted in favour of asking the precise
  question; a broad `403 → disable` would empty the catalogue an endpoint at a time.
- **Report what happened.** If a test fails, say so with the output. If a step was skipped, say
  that. "Deferring a test defers the phase" applies to progress reports too.

## Definition of done

A phase is done when its **exit criteria in [ROADMAP.md](docs/ROADMAP.md) verifiably pass** and its
tests are written. Not "it works on my machine". Deferring a test defers the phase — say so plainly
rather than marking it complete.

`make check` is the gate. It must be green, and it must have been run.

---

## MCP servers

Configured in `.mcp.json` (project-scoped):

| Server | Use for |
| --- | --- |
| `postgres` | Querying the local dev database — inspect schema, check what a game actually wrote |
| `playwright` | Driving the frontend in a real browser: verifying UI, screenshots |
| `context7` | Current library docs. Valuable here — Next.js 16, LiteLLM and Clerk all move fast |

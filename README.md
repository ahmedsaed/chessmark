# Chessmark

**LLM agents playing chess — against each other, and against you.**

Chessmark is two things at once:

1. **A benchmark.** Chess is a clean, adversarial, long-horizon agentic task. Models must use tools,
   maintain state across dozens of turns, reason about consequences, and never emit an invalid
   action. Chessmark measures how well they do it — legality, strength, cost, latency, and
   consistency — with everything recorded.
2. **A show.** Reasoning streams live next to the board, move by move, as the model produces it.
   You can sit down and play one yourself. Models can trash-talk — though not in a ranked game,
   where a fixed configuration is what makes the result mean anything.

Every token, tool call, reasoning trace, and taunt is persisted and replayable.

---

## Status

**Live and playing.** Models are paired continuously in an open pool, games stream as they happen,
and the leaderboard is built from them. The engine, the API, the site, ratings, tournaments,
human-vs-model play and deployment are all built and running.

What is not: **moderation** (Phase 11 — so conversation between models is off in ranked games),
**Stockfish analysis** (Phase 14 — no per-ply evaluation, and no engine anchoring the rating
scale), and **scheduled tournaments**, which are started by hand. [ROADMAP.md](docs/ROADMAP.md)
carries the phase list and, more usefully, its *Known gaps*.

| Document | What it covers |
| --- | --- |
| [VISION.md](docs/VISION.md) | What Chessmark is, who it's for, what success looks like |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | Functional + non-functional requirements |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data model, agent loop |
| [ROADMAP.md](docs/ROADMAP.md) | Phased delivery plan, exit criteria, and the known gaps |
| [TOURNAMENTS.md](docs/TOURNAMENTS.md) | Formats, fields, pools, eras, settling |
| [PROVIDERS.md](docs/PROVIDERS.md) | OpenRouter reality, the catalogue, the free tier |
| [TESTING.md](docs/TESTING.md) | The three suites and the rules they hold to |
| [FRONTEND.md](docs/FRONTEND.md) | Next.js, the design system, the event stream |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Containers, `./chessmark`, CD, backups |
| [CHANGELOG.md](CHANGELOG.md) | What shipped, and when |
| [adr/](docs/adr/) | Every decision, why it was made, and what we live with |
| [LICENSE](LICENSE) | Source-available: read it and contribute; do not run or sell it |

### How a result becomes a rating

Worth knowing before reading the leaderboard, because most of the interesting decisions are
exclusions:

- **A ranked game runs one fixed, versioned configuration** — a recorded prompt version and tool
  schema version, no personas, no chat. A game played under an older version measured a different
  task and is excluded rather than quietly mixed in
  ([ADR-0038](docs/adr/0038-a-prompt-version-has-two-parts.md)).
- **A harness bound is never a finding about a player.** Our ceilings, our budget, our provider's
  outage — those fail a turn, they do not forfeit a model
  ([ADR-0019](docs/adr/0019-harness-bounds-are-not-findings.md)). A forfeit for illegal moves or
  for never calling a tool *does* count: that is the benchmark's whole subject.
- **A rule that decides a game is stated in the prompt.** A model cannot be scored against a
  condition it was never told about
  ([ADR-0020](docs/adr/0020-claimable-draws.md)).
- **A pool carries eras.** It never ends, so a change to the prompt or the tools opens a new era
  inside it rather than replacing it, and the table shows the era being played
  ([ADR-0043](docs/adr/0043-a-pool-carries-its-eras.md)).

---

## Stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, `uv` |
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind |
| Database | PostgreSQL 16 |
| Queue / cache | Redis |
| LLM routing | OpenRouter via LiteLLM |
| Auth | Clerk |
| Chess rules | `python-chess` |
| Engine analysis | Stockfish — **not built yet** (Phase 14) |
| Live updates | Server-Sent Events |

---

## Layout

```
chessmark/
├── apps/
│   ├── api/          # FastAPI backend + agent runtime
│   └── web/          # Next.js frontend
├── docs/             # Vision, requirements, architecture, roadmap, ADRs
├── scripts/          # Anything run by hand, including anything that spends money
├── chessmark         # The server CLI — every command runs in a container
└── docker-compose.yml
```

## Quick start

Needs Docker, [`uv`](https://docs.astral.sh/uv/), and `pnpm`.

```bash
make setup                 # install dependencies, create .env from .env.example
make up                    # start Postgres and Redis
make migrate               # create the schema
make seed-models           # fetch the model catalogue from OpenRouter
make refresh-endpoints     # find which providers serve each model
make api                   # :8010
make web                   # :3010, in a second shell
make worker                # play model turns, in a third
```

Nothing plays on its own until a game exists: `make play ARGS="--scripted"` runs a complete game
with no key and no network, and `make tournament ARGS="field --free"` shows who would enter a pool.

Ports are **3010 / 8010 / 5433 / 6380**, deliberately not the defaults, so Chessmark can run
alongside other projects ([ADR-0012](docs/adr/0012-nonstandard-local-ports.md)).

Everything but `make seed-models` works without credentials. Put an
[OpenRouter key](https://openrouter.ai/keys) in `.env` to seed the catalogue and play real models;
`make play ARGS="--scripted"` plays a complete game with no key and no network.
[Clerk](https://dashboard.clerk.com) keys are optional — left blank the site runs signed-out, and
only starting a game is refused.

### Where the model list comes from

Two steps, and **both are needed before a model can be picked in the UI**:

| Step | Fetches | Writes | Cost |
| --- | --- | --- | --- |
| `make seed-models` | OpenRouter's catalogue — one request | `model_registry`: pricing, context window, tool and reasoning support | free |
| `make refresh-endpoints` | Each model's endpoints — **one request per model** | `model_endpoints`: which providers serve it, at which precision | free, but slow |

A model is registered by the first step and **playable only after the second**: a contestant is
`(model, quantization)` and the precision comes from the endpoint table
([ADR-0015](docs/adr/0015-quantization-as-identity-and-pinned-endpoints.md)), so a model with no
endpoint rows has no contestants and is filtered out of every picker. If the model list looks
short, it is almost always `refresh-endpoints` that has not been run.

Two kinds of model are **never registered**, because a model that cannot play should not be
offered. Production currently carries **266 playable models, 16 of them free** — the free number is
the one that matters day to day, since the open pool draws from it:

- **Without tool calling.** The runtime acts solely through tools (AGENT-01).
- **`:batch` variants.** Half price, and served *asynchronously* — a job submitted and collected
  later, not answered on the endpoint a turn calls. A turn blocks on a synchronous completion with
  a 600-second ceiling, so a batch model runs out the clock and **forfeits**, recording a loss
  against a model that never moved. Nothing in the data marks them: they declare `tools`, carry
  active endpoints, and report 99% uptime. All but one have a non-batch sibling.

Both commands are idempotent and safe to re-run on deploy. Neither deletes anything: a model that
disappears from the catalogue is *disabled* (`make seed-models ARGS=--disable-missing`), never
removed, so games that used it stay readable.

**There is no seed file.** The catalogue was once a committed `seeds/models.json`, and it silently
went stale — 239 models against 419 live, missing every frontier model, because the script that
wrote it defaulted to free models only. Pricing backs the spend caps in
[ADR-0011](docs/adr/0011-server-keys-layered-budgets.md), so a stale price is a wrong cap.

## Everyday commands

| Command | What |
| --- | --- |
| `make check` | Lint, typecheck, and the full test suite — the gate |
| `make play ARGS="--scripted"` | Play a complete game with no API key |
| `make dev-pull` | Replace the local database with production's, then run this branch's migrations |
| `make tournament ARGS="…"` | Fields, pools, standings — `field --free`, `create`, `standings` |
| `make psql` / `make redis` | Datastore shells |
| `make migration m="..."` | Generate a migration |
| `make drift` | Fail if models and migrations disagree |
| `make backup ARGS=--verify` | Dump, restore into a scratch database, compare, drop it |

`make dev-pull` is the one worth knowing about. It brings production's rows onto your machine and
runs your branch's migrations against them, so a change meets real data before production does —
emails and Clerk ids are scrubbed on the way in
([DEPLOYMENT.md](docs/DEPLOYMENT.md#testing-against-productions-data)).

On a server there is no toolchain: `./chessmark` runs everything in containers, and
`./chessmark help` lists it.

## Contributing

Issues and pull requests are welcome — bug reports, security findings, and proposed changes all
land in the same place. `make check` must pass before a change is done; see
[CLAUDE.md](CLAUDE.md) for the invariants a change must not break.

## License

**Source-available, not open source.** See [LICENSE](LICENSE).

You may read the code, study it, discuss it, and fork it *to prepare a contribution*. You may not
run it as a service, redistribute it, or put it to commercial use. That is a deliberate pair: the
code is public so the benchmark can be audited — a leaderboard nobody can inspect is not worth
much — while operating Chessmark stays with its author.

Contributions are licensed to the project under section 4 of the LICENSE, which is what keeps the
option of a commercial licence open. For any use the licence does not cover, ask; permission costs
nothing to request.

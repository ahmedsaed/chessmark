# Chessmark

**LLM agents playing chess — against each other, and against you.**

Chessmark is two things at once:

1. **A benchmark.** Chess is a clean, adversarial, long-horizon agentic task. Models must use tools,
   maintain state across dozens of turns, reason about consequences, and never emit an invalid
   action. Chessmark measures how well they do it — legality, strength, cost, latency, and
   consistency — with everything recorded.
2. **A show.** Models trash-talk each other mid-game. Reasoning streams live next to the board.
   You can sit down and play them yourself.

Every token, tool call, reasoning trace, and taunt is persisted and replayable.

---

## Status

🚧 **In development.** The engine, the API, the site, ratings and human-vs-model play are
built; deployment is not. See [ROADMAP.md](docs/ROADMAP.md) for what is done and what is next.

| Document | What it covers |
| --- | --- |
| [VISION.md](docs/VISION.md) | What Chessmark is, who it's for, what success looks like |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | Functional + non-functional requirements |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data model, agent loop |
| [ROADMAP.md](docs/ROADMAP.md) | Phased delivery plan with exit criteria |
| [adr/](docs/adr/) | Architecture Decision Records |

---

## Stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, `uv` |
| Frontend | Next.js (App Router), TypeScript, Tailwind |
| Database | PostgreSQL 16 |
| Queue / cache | Redis |
| LLM routing | OpenRouter via LiteLLM |
| Auth | Clerk |
| Chess rules | `python-chess` |
| Engine analysis | Stockfish (deferred phase) |
| Live updates | Server-Sent Events |

---

## Layout

```
chessmark/
├── apps/
│   ├── api/          # FastAPI backend + agent runtime
│   └── web/          # Next.js frontend
├── docs/             # Vision, requirements, architecture, roadmap, ADRs
├── scripts/          # Dev + ops scripts
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
offered — of ~419 models on OpenRouter, ~289 qualify:

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
| `make check` | Lint, typecheck, and the full test suite |
| `make play ARGS="--scripted"` | Play a complete game with no API key |
| `make psql` / `make redis` | Datastore shells |
| `make migration m="..."` | Generate a migration |
| `make drift` | Fail if models and migrations disagree |

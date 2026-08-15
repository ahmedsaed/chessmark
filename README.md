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

🚧 **Research & planning phase.** No application code yet. See [`docs/`](docs/).

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

Not yet — see [ROADMAP.md](docs/ROADMAP.md) Phase 0.

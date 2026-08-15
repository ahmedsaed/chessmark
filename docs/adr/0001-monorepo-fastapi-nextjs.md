# 0001. Monorepo with FastAPI + Next.js

**Status:** Accepted
**Date:** 2026-08-15

## Context

Chessmark needs a Python backend (the chess and LLM ecosystems live there: `python-chess`,
`litellm`) and a rich interactive frontend. The project owner is familiar with FastAPI and Next.js
and will guide rather than write most of the code, so familiarity with the shape of the codebase
matters more than novelty.

## Decision

A single repository containing `apps/api` (FastAPI, Python 3.12, `uv`) and `apps/web` (Next.js 16
App Router, TypeScript, Tailwind 4), with shared docs, a root `Makefile`, and one `docker-compose.yml`.

Tooling: `uv` for Python, `pnpm` for Node, `ruff` + strict `mypy` + `pytest` on the backend, `eslint`
+ `tsc` on the frontend. Everything runs from `make check`.

## Alternatives considered

- **Separate repos.** Cleaner boundaries, but every cross-cutting change becomes two PRs and a
  version-skew problem — painful for a project where one person owns both sides.
- **Full-stack Next.js with API routes.** One language, but Python's chess and LLM libraries are far
  better, and long-running agent turns fit poorly in serverless request handlers.
- **Nx / Turborepo.** Real value at many packages. At two, it's configuration overhead for nothing.

## Consequences

- One `git clone`, one `make setup`, one CI pipeline covering both sides.
- Types are duplicated across the boundary. Mitigation: generate a TypeScript client from FastAPI's
  OpenAPI schema in Phase 6, rather than hand-maintaining interfaces.
- Deployment is split (frontend possibly Vercel, backend on the VPS) even though the repo isn't.
  That's fine, but CI must build and deploy the two independently.
- `uv` is a newer tool. It's fast and lockfile-correct, and the fallback to plain pip/venv is
  straightforward if it ever becomes a problem.

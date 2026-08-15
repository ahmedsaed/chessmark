# 0012. Non-standard local ports

**Status:** Accepted
**Date:** 2026-08-15

## Context

During Phase 0 setup, `docker compose up` failed: port 5432 was already bound. The development
machine runs several other projects concurrently, occupying **3000** (a Next.js frontend), **8000**
(a FastAPI backend), **5432** (a Postgres container), and **6379** (a Redis container).

Stopping those containers to free the defaults would mean Chessmark cannot be developed at the same
time as anything else — an unacceptable daily tax for a cosmetic preference.

## Decision

Chessmark uses its own port block, everywhere, including in the committed defaults:

| Service | Port |
| --- | --- |
| Next.js | **3010** |
| FastAPI | **8010** |
| Postgres | **5433** |
| Redis | **6380** |

These are the defaults in `.env.example`, in `Settings` (`core/config.py`), and in the `dev`/`start`
scripts in `package.json`. Every port in `docker-compose.yml` is env-overridable.

## Alternatives considered

- **Stop the other containers.** Free, and wrong: it makes the machine single-project.
- **Keep defaults in config, override only in a local `.env`.** Config would then disagree with
  reality on the one machine that matters, and every fresh clone would fail the same way. Defaults
  should reflect the environment they're used in.

## Consequences

- Chessmark runs alongside every other project on this machine with no conflicts.
- Anyone reading the docs, the `Makefile`, or a curl example sees the correct ports.
- Production is unaffected — services sit behind Caddy on 80/443 regardless.
- The reasoning is recorded here and in `.env.example`, so the non-standard ports read as deliberate
  rather than as a mistake to be "fixed" later.

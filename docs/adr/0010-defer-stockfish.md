# 0010. Defer Stockfish, but ship the schema for it now

**Status:** Accepted
**Date:** 2026-08-15

## Context

Engine analysis would add real value: per-move centipawn loss, blunder classification, accuracy
percentages, and — via capped-Elo engine opponents — an absolute anchor for the rating scale.
Head-to-head Glicko-2 alone produces ratings that are only meaningful *relative to each other*.

The integration itself is modest: `python-chess` ships a UCI driver, so a capped-strength opponent is
roughly 20 lines and per-ply annotation roughly 60. The real costs are a system dependency and CPU
time — roughly 1–2 minutes per game at depth 18.

## Decision

Defer the Stockfish work to its own phase (Phase 14), but **build the schema for it now** (Phase 2):

- `plies` carries nullable `eval_cp`, `cp_loss`, and `classification` columns from the first
  migration.
- An `analysis_jobs` table exists from the start.
- When it lands, Phase 14 is purely additive — an annotation worker and an engine player — with no
  migration churn and no refactoring.

Analysis runs strictly as a **post-game background job**. It is never in the live game path.

## Alternatives considered

- **Build it early.** Every game would have quality metrics from day one, with no backfill. But it
  adds a dependency and a worker before the agent runtime — the actually hard and novel part — has
  been proven.
- **Skip it entirely.** Cheapest, but leaves the ratings unanchored and discards the per-move quality
  metrics, which are among the most interesting outputs the project can produce.

## Consequences

- Phases 1–5 stay focused on the novel problem: making agents play chess reliably.
- Historical games are backfillable, because the schema was there all along. Nothing is lost by
  waiting.
- Live play is never blocked on engine CPU, by construction.
- Until Phase 14, ratings are relative-only. The methodology page must say so plainly rather than
  implying an absolute scale.
- Analysis must be deterministic for a fixed engine version and depth, and the engine version must be
  recorded per job — otherwise a Stockfish upgrade silently makes old and new annotations
  incomparable.

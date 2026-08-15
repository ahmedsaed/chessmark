# 0007. The turn is the unit of work

**Status:** Accepted
**Date:** 2026-08-15

## Context

A model turn takes 2–60 seconds and costs real money, so it cannot run inside an HTTP request. It
needs a background worker. The question is what a queued job represents.

The obvious choice — one job per **game**, looping internally until the game ends — is simple, but
it makes a worker crash catastrophic: the game is mid-flight in a dead process, with no clean way to
know where it got to or how to resume.

## Decision

A job is **"advance game G from ply N"**, not "play game G".

- Each job carries an `expected_ply`. On pickup, the worker compares it to the game's committed ply
  count. If they differ, the job is a **no-op** — the game already moved on.
- After a ply commits, the worker enqueues the next `advance_turn` job.
- A human move is committed by the API and then enqueues the identical job type.

## Alternatives considered

- **One long-running job per game.** Simpler to write, but resumption after a crash requires
  reconstructing in-flight state that was never persisted.
- **In-process asyncio tasks in the API.** No queue needed, but a deploy or restart kills every live
  game, and the API tier stops being stateless.
- **Polling loop over "games needing a move".** Resilient, but adds latency and wasteful scanning.

## Consequences

- **Idempotent by construction.** At-least-once delivery is fine. Redelivery is harmless.
- **Crash-resilient.** Kill a worker mid-turn: the ply was never committed, the job is redelivered,
  the turn simply reruns. Worst case is one LLM call paid for twice — an acceptable price for never
  corrupting a game record.
- **One code path** for human-vs-model and model-vs-model. This removes an entire category of
  divergent-behaviour bugs before it exists.
- **Horizontally scalable.** Add worker replicas; different games run on different workers.
- Cost: every turn re-loads the game and rebuilds the transcript from Postgres. That's a real
  overhead, but it is small next to an LLM call and it is what makes statelessness possible.
- A stalled game (job lost, Redis restarted) needs a reconciler that re-enqueues games whose last
  event is stale. That is explicit Phase 5 work, not an afterthought.

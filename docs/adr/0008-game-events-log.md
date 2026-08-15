# 0008. A single `game_events` log powers live, reconnect, and replay

**Status:** Accepted
**Date:** 2026-08-15

## Context

Three features want the same information in three different shapes:

1. **Live spectating** — push state changes as they happen
2. **Reconnection** — deliver exactly what was missed during a network drop
3. **Replay** — scrub to any ply and show the state at that moment

The tempting path is three mechanisms: a pub/sub payload for live, a diff endpoint for reconnect,
and joins across `plies`/`turns`/`messages` for replay. Three mechanisms mean three chances for them
to disagree about what happened — and a replay that doesn't match what spectators saw is a bug
nobody will be able to reproduce.

## Decision

One append-only table, `game_events`, with a monotonic per-game `seq`. Every state change appends
exactly one row: `game_started`, `turn_started`, `thinking`, `tool_called`, `illegal_attempt`,
`move_made`, `message_sent`, `draw_offered`, `game_ended`.

- **Live:** the worker appends to Postgres, then publishes to Redis; SSE forwards it.
- **Reconnect:** the client sends `Last-Event-ID: <seq>`; the API replays rows where `seq > last`,
  then attaches to the live stream.
- **Replay:** the same rows, fetched in bulk and scrubbed client-side.

Postgres is the durable record. Redis is only the notification path — a dropped Redis message costs
latency, never data.

## Alternatives considered

- **Separate mechanisms per feature.** More direct for each individually, but they drift apart, and
  the drift shows up as unreproducible inconsistencies.
- **Reconstructing state by replaying `plies` alone.** Loses everything that isn't a move: reasoning
  starts, tool calls, illegal attempts, chat. Those are most of what makes a replay worth watching.
- **Event sourcing as the primary model** (deriving `games`/`plies` from events). More pure, but it
  makes ordinary queries — the leaderboard, a game list — needlessly hard.

## Consequences

- Live and replay are guaranteed consistent, because they are literally the same rows.
- Reconnection is nearly free, and gap-free by construction.
- The event log is the natural debugging artefact: "what did this game actually do?" is one query.
- `seq` must be gap-free under concurrency. Phase 2 has an explicit 100-concurrent-append test.
- Write amplification: an active game appends many event rows. Acceptable — they're small, and the
  table is append-only with a clean `(game_id, seq)` index.
- Event payload shapes become a compatibility surface consumed by the frontend. They need versioning
  discipline, like any API.

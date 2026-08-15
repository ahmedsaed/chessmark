# 0004. SSE instead of WebSockets for live updates

**Status:** Accepted
**Date:** 2026-08-15

## Context

Spectators need live updates: moves, reasoning, trash talk. The traffic is overwhelmingly
**one-directional** — the server pushes, the client watches. The only client→server actions are a
human's own move and chat, which are ordinary infrequent HTTP requests.

## Decision

Server-Sent Events over a plain `GET /games/{id}/events`. Client actions use normal REST.

Reconnection uses the standard `Last-Event-ID` header against the `game_events` table
(see [0008](0008-game-events-log.md)), so a dropped connection resumes with zero gaps.

## Alternatives considered

- **WebSockets.** Necessary if we had high-frequency bidirectional traffic. We don't. They cost a
  separate protocol, manual heartbeats, hand-rolled reconnection and message-ordering logic, and
  more awkward proxy and load-balancer configuration.
- **Polling.** Trivially simple and ships fastest, but a 1–2s poll feels dead in a product whose main
  appeal is watching something unfold.

## Consequences

- `EventSource` is browser-native: automatic reconnection and `Last-Event-ID` come free.
- It's just HTTP — proxies, Caddy, curl, and browser devtools all work without special handling.
  `curl -N` is a genuine debugging tool here.
- Browsers limit concurrent HTTP/1.1 connections per origin (~6). Over HTTP/2, which we'll serve,
  this is not a practical constraint.
- SSE is text-only. Fine — all our events are JSON.
- If we later add something genuinely bidirectional and high-frequency (live spectator chat at
  scale), this may need revisiting. Spectator chat is explicitly deferred (TALK-07), so that day is
  not close.

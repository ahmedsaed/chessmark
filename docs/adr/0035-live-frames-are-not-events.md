# 0035. A turn streams as it happens, and what streams is not an event

**Status:** Accepted
**Date:** 2026-09-10
**Extends:** [0008](0008-game-events-log.md) — the event log stays the only record and the only
thing replay reads. Constrained by [0007](0007-turn-level-jobs.md), whose one-transaction-per-turn
rule is why nothing could be published mid-turn, and by invariant 7, which reserves a `game_events`
row for a state change. Reinforces invariant 8 via [0025](0025-reasoning-withheld-mid-game.md)'s
read-time gate.

## Context

A turn is one transaction. The worker runs every provider round inside it, appends each event as it
goes, and publishes only after the commit — *"Committed. Only now is it safe to tell anyone about
it."* That is correct about durability and it is the whole of the problem on screen.

Ply 8 of `e601f9af` took **632 seconds** across six provider rounds:

    round 1     1.1s      round 4    220s
    round 2    10.9s      round 5    369s
    round 3    29.0s      round 6      2.8s

Fifteen events came out of it, in the right order, every one of them stamped `00:20:44.847132`. A
spectator watched a still board for ten and a half minutes and then received the entire turn in one
frame. Two distinct latencies hide in that:

* **Between rounds.** Round 3 finished at 41 seconds and its reasoning was known then. Nothing
  about the transaction requires *withholding* it for the next nine minutes — only that it must not
  be written down as settled, because a later round can still fail and roll the turn back.
* **Inside a round.** The 369-second round produced 18,687 reasoning tokens, and the provider was
  emitting them the whole time. We asked for none of it: the gateway calls `acompletion` without
  `stream=True`.

The obvious fix — publish events as they are appended — is not available. They are uncommitted. A
turn that fails rolls back whole, and a spectator who had already been shown its reasoning would
have watched a turn that, in the record, never happened.

## Decision

**Two channels, and only one of them is durable.**

| | durable events | live frames |
| --- | --- | --- |
| stored in | `game_events`, committed at turn end | nowhere — Redis pub/sub only |
| identity | `seq`, gap-free per game | none |
| drives | replay, reconnect, backfill, the board | the live cursor, and nothing else |
| on rollback | never existed | already sent, and superseded |
| SSE frame | `event: <type>` with `id: <seq>` | `event: delta`, **no `id:`** |

A live frame is not a state change, so invariant 7 is untouched: it stays one `game_events` row per
change, appended in the same transaction as the change. A live frame is a *prediction* that the
turn will commit — usually right, occasionally wrong, and never the record.

**The durable events supersede the frames.** When the turn commits, the same content arrives with
`seq` numbers, and the client drops its provisional blocks in favour of them. A reconnecting client
gets exactly what it gets today, because `Last-Event-ID` still names a committed event — which is
why live frames carry no `id:`. A rolled-back turn simply never sends the durable version, and the
provisional blocks are discarded when the turn is retried.

**Two rungs, because they carry different risk.**

*Round boundaries* need no provider change at all. After each round the runner publishes what that
round produced. The 632-second turn becomes six frames arriving over ten minutes instead of one
frame at the end, and the provider call is byte-identical to today's.

*Token deltas* need `stream=True`, and that is the rung that can lose the record. LiteLLM's
streaming path reads `reasoning_content` and drops `reasoning`, so on several providers the
thinking chunks never arrive
([#21386](https://github.com/BerriAI/litellm/issues/21386),
[#20246](https://github.com/BerriAI/litellm/issues/20246)) — a silent invariant-3 violation dressed
as a UI feature. So streaming is **off by default, per endpoint**, and turning it on for one is an
operator decision made after `make smoke-llm` shows that model's reasoning surviving the round
trip. Usage arrives only in the final chunk, so `stream_options={"include_usage": true}` is not
optional either: without it every cost silently becomes zero (invariant 4).

**A frame is redacted exactly like an event.** Invariant 8 — a person must not read their opponent's
reasoning mid-game — and this is the fastest path by which they could. The stream computes
`must_withhold_thinking` once per connection and drops reasoning frames entirely for that reader,
rather than emptying them: an empty block renders as a model that thought nothing.

## Consequences

A spectator sees a turn assemble. That is the whole point, and it is also the first time this
project has shown anyone something that is not yet true — a frame can describe a round of a turn
that then fails. The mitigation is that it is unlabelled as history: provisional blocks live only
in the open turn, and the commit replaces them.

`game_events` does not grow. A turn that emitted 40,000 reasoning tokens through the delta channel
still appends exactly the events it appends today, because a frame is never written down.

A client that ignores `delta` frames is correct and complete. Replay, the PGN, the leaderboard and
every test that reads the log are unaffected, which is the property that makes this safe to ship
with the streaming rung switched off.

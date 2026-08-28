# 0019. A harness bound is not a finding about a player

**Status:** Accepted
**Date:** 2026-08-28
**Amends:** [0015](0015-quantization-as-identity-and-pinned-endpoints.md) — the routing lottery it
removed had reappeared as a clock. Implements AGENT-17 and AGENT-18.

## Context

A game ends with a `Termination`, and the leaderboard reads that value to decide whether the result
is a **finding about a player** (a forfeit counts: agentic reliability is the measurement) or
something **we** did (a harness stop, excluded from ratings). The distinction is the benchmark's
whole claim to honesty.

Two terminations were on the wrong side of it, and one free-model pool made the cost plain: **five
of twelve completed games carried a verdict neither model had earned.**

**`TIMEOUT` measured the provider, not the player.** The same model on two endpoints got two
verdicts — precisely the routing lottery ADR-0015 exists to remove, reappearing as a clock. One
model lost a game *at ply 1 having never been served a single completion*. A slow provider is a real
fact worth publishing, but "it played worse" is a different claim, and of a slow provider it is
false.

**`BUDGET_EXCEEDED` counted the prompt**, and the prompt is re-sent on every round-trip (ADR-0003),
so the ceiling measured transcript size times round-trips rather than anything the model produced. A
model that generated **5,263** tokens was forfeited for "using 514,446" — four replays of a 128k
transcript. It punished long games hardest and large-context models most: a 1M-window model at a
128k prompt is nowhere near compaction's trigger (ADR-0018), and four round-trips still crossed a
flat 400k.

Separately, a model can be **refused outright**. `thinkingmachines/inkling-small:free` and
`thinkingmachines/inkling:free` answer 403 *"only available on agentic harnesses. Try plugging it
into a coding agent or productivity app listed on openrouter.ai/apps"*. That is a distribution
allow-list, not a capability check, and **nothing in the catalogue predicts it**: the model
advertises `tools`, a 1M window, `status: 0`, 100% uptime and `per_request_limits: null` —
indistinguishable from one that works. One pool spent **22 pairings** dying at ply 0 against it,
because a generic error taught the matchmaker nothing.

There is also nothing we can declare to satisfy it. The gate was probed four ways — no headers,
`HTTP-Referer` + `X-Title`, `X-OpenRouter-Title`, and `X-OpenRouter-Categories: agents` — and
answered 403 identically. OpenRouter's [app-attribution docs](https://openrouter.ai/docs/app-attribution)
describe those headers as being "for rankings on openrouter.ai" with no registration step, and
`/apps` is a usage leaderboard rather than a list one applies to. What settles it: the **paid**
variant of the same model answers on the first try, so the gate is on the free distribution and not
on our client.

## Decision

**A ceiling the harness imposes fails the turn; it does not forfeit the player.**

- `TIMEOUT` and `BUDGET_EXCEEDED` leave `FORFEIT_TERMINATIONS`. `TIMEOUT` joins
  `RESUMABLE_TERMINATIONS`, and a turn that runs out of clock is marked `FAILED` so the worker
  retries it.
- The per-game budget counts **completion tokens only** (`max_completion_budget`), so it measures
  what the model produced.
- Latency and transcript size stay measured and published. They are statistics, not verdicts.

**A model the provider will not serve us is withdrawn, not paired again.** 403 joins 429 and
provider-404 as *unavailability* rather than a bad request. A refusal whose wording names an
allow-list is treated as a **gate**: `worker._disable_gated` sets `enabled = False` on the registry
row and abandons the game immediately.

A gate is the one unavailability that waiting cannot fix, so it is the one that does not pause.
Cooling the endpoint down is not enough — the ladder's first rung lapses after sixty seconds
(ADR-0017) — and pausing would spend the full 24-hour window rediscovering the same 403.

The model is **disabled, never deleted**: `players.model_id` is `ON DELETE RESTRICT`, and a game
must stay readable however its model turned out. A later catalogue sync will not undo it, because
`enabled` is written on creation only.

**Only the allow-list wording withdraws a model.** A plain 403 still pauses. Disabling on any 403
would empty the catalogue an endpoint at a time.

## Consequences

**Four frozensets classify a termination, across two modules, and they must agree.**
`FORFEIT_TERMINATIONS` and `RESUMABLE_TERMINATIONS` (`game/referee.py`) decide whether an ending is
a finding and whether it can be reopened; `HARNESS_TERMINATIONS` and `RATED_TERMINATIONS`
(`bench/ratable.py`) decide whether it reaches the leaderboard.

Nothing linked them, and this ADR's own change proved why that matters: the first pair was updated
and the second was not, so `timeout` stopped being called a forfeit and **went on being rated
anyway** — the half that actually reaches the leaderboard, which was the entire point. Its own test
listed it under "a forfeit counts". An audit found it, not a game.

`tests/bench/test_classification.py` now enforces the relationships: every termination is
classified, nothing is both rated and a harness stop, every resumable ending is a harness stop and
unrated, and every forfeit is rated and final. **A new termination cannot be added without
classifying it.**

**A gate is learned, not predicted**, so the first pairing against a newly gated model is still
wasted. That is the price of there being no metadata to filter on, and one pairing is a great deal
better than twenty-two.

`./chessmark prune --model <slug> --only-named` clears such a model's records after the fact — the
eligibility rule cannot see a gate, so the operator supplies the finding the catalogue cannot.

**A closed event still cannot be resumed cleanly.** Abandoning its last pairing completes the event,
`advance` returns *already over*, and no later tick settles anything. A pool never finishes, which
is where this was needed. Recorded rather than fixed.

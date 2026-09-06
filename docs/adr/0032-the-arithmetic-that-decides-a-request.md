# 0032. The arithmetic that decides whether a request can be sent

**Status:** Accepted
**Date:** 2026-09-06
**Amends:** [0031](0031-a-turn-may-not-inflate-its-own-context.md) — which stopped a turn inflating
its own context and did not fix the arithmetic that decided the request in the first place.
[0024](0024-endpoint-output-ceilings-are-not-findings.md) — the attribution rule, which turned out
to depend on the registry being *wrong*. [0021](0021-measured-windows-and-the-compaction-ladder.md)
— the unmeasured bound.

## Context

ADR-0031 shipped. Most of it worked: `10fc99f0` finished a game whose last turn absorbed three
truncations and grew by 22,389 tokens where a pre-fix turn had grown by 114,874; four paused games
played to a result; no entrant held two pairings at once. Three games did not recover, and the
reasons were three different pieces of arithmetic.

### The reactive rung was double-counting

`29e7f004` and `e601f9af` were reopened three times each and died **one second** after every
attempt. A summarising call to a free model averages sixteen seconds, so nothing was called.

When an endpoint refuses a request for size it reports a total, and that total includes the
`max_tokens` we asked it to reserve:

```
227,440 text + 502 tool + 64,000 output = 291,942     <- the 400's own figure
```

`_compact_reactively` passed that total on as "how full is the window". `_summarise` then asked for
room to write a 2,000-token summary:

```
256,000 - 291,942 - framing  ->  negative  ->  NoRoomToAnswerError  ->  no summary
256,000 - 227,942 - framing  ->   27,802   ->  thirteen times what a summary needs
```

Without a summary the pass falls back to the trim-only rung, and on a transcript already folded to
one retained turn there is nothing left to trim — so `Plan.worthwhile` was false, `_compact`
returned having done nothing, and the refusal was re-raised. The games were not failing to
recover; they were never attempting to.

### The attribution rule depended on the registry being wrong

Poolside stops `laguna-s-2.1` at 32,768 output tokens. While the catalogue was stale we asked for
64,000, the model produced 32,768, and `32,768 < 64,000` read as *the endpoint's* limit — which
earns a nudge and up to `MAX_TRUNCATIONS` retries. It usually recovered.

Then the catalogue was refreshed. `max_completion` became the true 32,768, we asked for exactly
that, and the identical response read as *ours*: `_our_ceiling_bound` returned true, the turn failed
on sight with no nudge, five job attempts followed, and `a016a326` was abandoned at ply 72 having
just played plies 68 through 72 perfectly well.

**Same model, same behaviour, opposite verdict — because we got the number right.**

### Two bounds sized for windows we no longer run against

`FRAMING_TOKENS` was 256: one thousandth of a 256,000-token window, against a count taken on the
provider's side after a serialisation we never see. Comparable agents hold back 4,096.

`FIRST_CALL_FRACTION` asked for half the window when nothing had been measured, defended as a bound
that "always fits" because a system prompt plus one turn prompt is a few thousand tokens. True of a
game's genuine first call, and false of a resumed game carrying 227,440 tokens and a persisted
`last_prompt_tokens` of zero — where half a window asked for 64,000 and the refusal ended the game.

### A rescue inside a transaction that rolls back

A turn is one transaction (ADR-0007), so a compaction inside a failing turn is undone with it. Even
once the reactive rung could act, its work would not survive the turn it was rescuing.

## Decision

**The reactive rung compacts against the prompt, not the whole request.** Where the endpoint spells
out its breakdown — *"(227440 of text input, 502 of tool input, 64000 in the output)"* — those
components are parsed and used. Subtracting our own `max_tokens` from the total is the fallback and
is deliberately second: it assumes the total reserved exactly what we asked for, and where that is
untrue it *under*-counts the prompt, which is the direction that earns a second refusal.

**A truncation is failed on sight only when our request was unanswerable.** The test is the size of
what we allowed — at or below `MIN_USEFUL_COMPLETION` no answer fits and there is nothing to nudge
— not whether the response happened to reach it. This keeps ADR-0021's protection, where a
miscalculated window asked for a single token, and removes the perverse dependence on the registry
being inaccurate. Neither branch is ever a finding about a player.

**`FRAMING_TOKENS` becomes 4,096**, matching what comparable agents hold back against windows of
this size.

**An unmeasured call holds back the reserve** rather than half the window — 25,600 against 256,000
where the old bound asked for 128,000. It is not a guess at the prompt either way, and it cannot
guarantee a fit; what it buys is that the refusal becomes rare, and the reactive rung now recovers
from the ones that remain.

**The last rescue runs outside the turn.** On a context-length rejection the worker elides stale
tool output in a session of its own and requeues once. Trim-only, because folding needs the provider
that just refused us, and this rung needs no provider at all.

## Consequences

Two games become recoverable that three resumes could not touch, and one class of abandonment
— a model that fills a correctly-declared ceiling — stops happening.

Requests are slightly smaller across the board. An unmeasured call asks for the reserve rather than
half a window, and every measured call holds back 4,096 tokens instead of 256. The cost is a
marginally shorter answer in the rare case a prompt sits within 4k of the wall; the benefit is that
arithmetic which is *nearly* right stops being fatal.

**Truncations become more expensive to hit and cheaper to survive.** A model handed a usable budget
now costs up to `MAX_TRUNCATIONS` extra calls before its turn fails, where it previously failed on
the first. That is the trade: three calls against an abandoned game at ply 72.

The wider practice on `finish_reason: "length"` is to continue — append the truncated output and
ask the model to carry on. Ours differs on purpose: the deliverable is a tool call, not prose, and
appending the fragment is precisely what ADR-0031 exists to prevent. The nudge is that pattern
adapted — retry with guidance, and drop the text the model cannot act on.

Nothing here revisits ADR-0024's conclusion that an endpoint's ceiling is not a finding about a
model. What changed is only how quickly we stop trying.

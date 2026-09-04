# 0031. A turn may not inflate its own context, and filling the window is not a finding

**Status:** Accepted
**Date:** 2026-09-04
**Amends:** [0021](0021-measured-windows-and-the-compaction-ladder.md) — the half of it that kept
`CONTEXT_EXCEEDED` rated, and the reset that discarded a measurement after every fold. Extends
[0018](0018-context-compaction.md) with the rule compaction was missing: folding a transcript is
worthless if the same turn is free to refill it. Follows
[0019](0019-harness-bounds-are-not-findings.md) and
[0024](0024-endpoint-output-ceilings-are-not-findings.md) to their conclusion.

## Context

`pool-free` abandoned 17 of 65 pairings. Two of them — `29e7f004` and `e601f9af` — died on a 400
from the provider, were reopened by hand twice each, and died on the same 400 within seconds both
times. This ADR is about those two, and about what the investigation found underneath them.

### One turn, ten calls

Turn 1041 of `29e7f004`, in order, as `llm_calls` recorded it:

| finish_reason | completion | prompt_tokens |
| --- | ---: | ---: |
| `stop` | 466 | 731 |
| `length` | **32,768** | 416,942 |
| `tool_calls` | 629 | 449,772 |
| `length` | **32,768** | 450,558 |
| `tool_calls` | 2,904 | 482,785 |
| `tool_calls` | 122 | 485,842 |
| `tool_calls` | 1,633 | 486,121 |
| `stop` | 660 | 487,910 |
| `length` | **32,768** | 484,047 |
| `length` | **32,768** | **516,877** |

Compaction ran at the top of that turn and worked — the prompt starts at 731 tokens. The same turn
then put half a million back.

The model was operating its tools correctly; four of the ten calls carry `tool_calls`. What it
could not do was finish a thought. Every attempt to reason at length was cut off at exactly 32,768
tokens, and each unfinished fragment was appended to the transcript and re-sent with the next
request. `nemotron-3-nano-omni-30b-a3b-reasoning` produced **47 such truncations across the pool,
every one of them at the identical value**, against prompts ranging from 100k to 517k.

**Each failed attempt made the next one harder.** That is the whole mechanism, and nothing in the
harness was watching for it. Compaction was deliberately once per turn (ADR-0018), which is a
sound rule for a turn that makes three round-trips and a useless one for a turn that makes twenty.

### The reset that made a resume futile

`_compact` set `self._prompt_tokens = None` and `player.last_prompt_tokens = 0`, on the reasoning
that the folded prefix no longer matched the measurement. True, and it threw away the only thing
known. `completion_cap` then took its unmeasured path, whose bound is half the window — a figure
sized for a game's genuine first call, where the prompt is a few thousand tokens (ADR-0021).

On a resumed game at ply 18 holding 227,440 tokens, half a window is not a conservative bound:

```
227,440 text + 502 tool + 256 framing  =  228,198
256,000 − 228,198                      =   27,802 tokens of room
completion_cap(227942, 64000)          →   27,802   ✓ fits
completion_cap(None,   64000)          →   64,000   ✗ 400
```

Because the reset was persisted, a reopened game began blind — and `should_compact` is gated on
having a measurement, so it could not compact its way out either. It re-threw the same 400 ten
seconds after being reopened. Twice.

### Summaries that stop mid-sentence

Fifteen summarising calls across the pool returned `finish_reason: "length"` at the 2,000-token
cap, eight of them from one model. Unlike every other truncation, a summary is never retried: it is
written into the transcript as that game's memory of itself and replayed for the rest of the game.

## Decision

**A truncated reply that reached no tool call is elided from the replayed transcript.** The message
keeps its place, its role and its structure — nothing is orphaned, and a provider that requires a
well-formed alternation still gets one — and carries `TRUNCATED_PLACEHOLDER` instead of the
fragment. `transcript_messages.truncated_at` records the decision; `content` and
`reasoning_details` still hold exactly what arrived, and `llm_calls` still holds the raw response,
so invariant 3 is untouched. Only the request shrinks.

This is deliberately narrow. Replaying a model's own reasoning is load-bearing and stays that way:
Gemini 3 refuses a function call whose `thought_signature` is missing and DeepSeek refuses a
thinking-mode history without `reasoning_content`. A fragment carrying **no tool call** has nothing
to be load-bearing for — there is no call to justify and no signature to pair — and the model does
not resume from it. The retry starts a fresh reasoning pass with the fragment sitting in front of
it as prose it must now read past. The placeholder says what happened, which is the part that
helps.

**The pre-fold measurement is kept as a bound rather than discarded.** A fold removes messages and
adds at most `SUMMARY_MAX_TOKENS`, so the size afterwards cannot exceed the size before plus the
summary. That is a bound derived from a measurement, not the estimate AGENT-19 forbids, and it is
persisted so a resumed game is never blind.

**Compaction may fire again when the transcript has grown since the last fold.** The guard becomes
"has anything been appended since?" rather than "have we folded?", read from the append-only
`transcript_seq`. A pass that frees nothing still fires only once, because nothing was appended.

**A summary that hits its cap is discarded**, and the pass falls back to the trim-only rung, which
needs no provider at all. A briefing that ends mid-word is worse than no briefing: the model acts
on it as though it were complete.

**`CONTEXT_EXCEEDED` moves to `HARNESS_TERMINATIONS` and becomes resumable.** While the agent had
no way to shrink its own history, filling the window was something the model did. Compaction
changed what the ending means: reaching the wall now says our fold did not keep up. It is the same
judgement ADR-0019 asks for everywhere else, and the same one that moved `TIMEOUT` and `TRUNCATED`.

## Consequences

Both games become playable again on their next resume, without intervention.

The cacheable prefix stops absorbing dead weight. A truncated fragment landed in it permanently
(invariant 2), so every later call in the game paid for a thought the model never finished.

**No cap was added, and that is the point.** A verbose model that calls tools is untouched: nothing
it emits is dropped, `max_tool_iterations` is unchanged, and no token budget was introduced. What
is bounded is the specific pathology of a turn re-sending text that produced nothing — which is not
a property of the model at all.

The ratings do not move. `TRUNCATED` and `ABANDONED` were already harness terminations, so none of
these games ever scored against anyone; C1 closes the remaining gap rather than repairing damage.
What was lost was **measurements** — 17 pairings that produced nothing, and 17 opponents who lost a
scheduled game alongside them.

We continue to ask for more output than two endpoints will produce. `max_completion_tokens` is
advertised (65,536 for the Nvidia endpoint) and synced by `registry.py`; the table was simply
stale, which OPS-23's scheduled refresh now prevents. Clamping to the true ceiling would not have
stopped the truncation — the model emits what it emits — and attribution is already correct on both
branches, so no observed-value column was added. The residual risk is an endpoint that *rejects* an
over-large `max_tokens` rather than truncating, which is ADR-0016's original failure and is caught
by the reactive rung.

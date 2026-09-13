# 0039. The window is sized for the request in front of us, not the one behind it

**Status:** Accepted
**Date:** 2026-09-13
**Amends:** [0021](0021-measured-windows-and-the-compaction-ladder.md) — the half of it that
forbade all local sizing, and the single regex its reactive rung depends on. Extends
[0033](0033-a-tail-budget-in-tokens-and-a-provider-that-cannot-count.md), which introduced the
per-conversation rate this decision puts to a second use.

## Context

Game `ed491262` was abandoned at ply 43 on a 400 that needed no interpretation:

    The request is 290310 tokens long and exceeds this model's context length of 262144 tokens.

Nothing was unknown. `model_endpoints.context_length` held **262,144** for `nex-n2.5-mini:free` on
Nex AGI — the endpoint's own figure, not the model's advertised one, read through the lookup
ADR-0021 built for exactly this. The seat was pinned to that endpoint and every call recorded it.
The window arithmetic was right. The game died anyway.

### Every decision read the previous request

`headroom_needed` is `max(reserve, 10% of window)`, so this seat folds above 235,930 tokens. Its
last measurement was **195,503** — 40,427 below the trigger. `should_compact` said no, correctly,
about a transcript that no longer existed. The same number then sized the answer:

```
available = 262,144 − 195,503 − 4,096 (framing)  =  62,545
```

and 290,310 − 62,545 leaves **227,765**: what the prompt actually was. The seat had added 32,262
tokens inside one turn against a guard band 26,214 wide. It stepped clean over it, and **compacted
zero times in 43 plies**.

The step size is not exotic. This seat replayed 565,682 reasoning tokens across 21 turns — around
27,000 per turn, stored and re-sent because Gemini 3 and DeepSeek require it (ADR-0031). A model
that reasons at length grows its own prompt by more than a tenth of the window every time it moves,
and the threshold is checked once per round-trip against a number from the previous one.

### The growth was measured and then discarded

`turn.py` counted the characters of the outgoing message list on the line above the decision:

```python
self._sent_characters = compaction.sent_characters(rows)   # the request in front of us, counted
occupied = self._prompt_tokens                             # the previous request's token count
... window.should_compact(occupied) ...
... window.completion_cap(occupied, ...) ...
```

`players.last_prompt_characters` already holds the character count that the stored measurement
belongs to — the pair is written together, from one request, at one instant, precisely so their
quotient means something. `_tokens_per_character` already computes it. It was allowed to decide
**where** to cut the retained tail and never **whether** to cut, or how much output to ask for.

### The backstop could not read the refusal

ADR-0021 installed a reactive rung: a 400 naming the window is compacted against the provider's own
numbers and retried once, which is what makes being wrong between measurements recoverable rather
than fatal. It never ran. `context_limit_from` matched one pattern —

    maximum context length is (\d+) tokens ... requested about (\d+)

— and Nex AGI does not say that. `context_limit_in` returned `None`, `_compact_reactively` bailed
at `if limit is None`, and the turn was abandoned with `attempts=1`. **An abstention was
indistinguishable from "this was some other 400"**, so the harness could not tell that its own last
line of defence had declined to act.

## Decision

### The figure both decisions read is projected onto the outgoing request

```python
projected = max(measured, round(measured × characters_now / characters_then))
```

`should_compact` and `completion_cap` take `projected`. Nothing else changes: the threshold, the
reserve, the ladder and the rungs are all as ADR-0021 left them.

**This is not the estimate ADR-0021 deleted.** That one was a universal constant — characters over
3.5 — driving a counter that reset to zero every turn, and it claimed 477,155 tokens for a six-ply
transcript before asking an endpoint for one output token and forfeiting a model for the
truncations. This is one measurement divided by another, taken on this conversation and this
endpoint, replaced by the provider's own count a round-trip later. It bridges two measurements; it
does not stand in for one.

**It may only ever raise the figure.** The `max` is the whole safety argument: a rate that drifts
low cannot reproduce the failure it exists to prevent. It also answers the case the rate genuinely
cannot describe — straight after a fold, the stored token figure is a *bound* on the pre-fold size
while the characters are post-fold, so scaling one by the other would be inventing room.

**And it is clamped to a sendable prompt**, exactly as the post-fold bound already is. The
conversion is multiplicative, so a stored pair that does not describe one transcript — a row from
before the pair was written together, or a seat whose content changed character — scales the whole
figure rather than only the part that is new. Unclamped, that turns a *prediction* into a
`NoRoomToAnswerError` and fails a turn the endpoint might well have accepted, which is the harness
convicting on a guess; two existing tests caught exactly that, both of them pairing a synthetic
token count with a transcript whose characters it does not describe. Clamped, the worst case is a
small `max_tokens` on a request that still goes out, with the endpoint's refusal and the reactive
rung behind it. The party that can actually count stays the authority.

The projection is never persisted either: `players.last_prompt_tokens` keeps holding only what a
provider returned.

**An unmeasured seat stays unmeasured.** No measurement, no projection, and `unmeasured_cap` holds
back the reserve exactly as before. A rate with nothing underneath it is the thing we are not doing.

### The rule ADR-0021 should have written

Invariant 4 governs **cost**, and cost is a claim about the past that must be exact. "Will this
request fit" is a claim about a request that has not been sent yet, checked by the provider's own
count one round-trip later, and it was never the same kind of question. ADR-0021 collapsed the two
because both touch characters. The narrower rule — *money is measured; a prediction may be made
from measurements, must be conservative, and must be replaced by a measurement as soon as one
exists* — is what the code now follows, and what ADR-0033 was already doing in one corner.

### The event log keeps reporting a number somebody returned

`occupied_tokens` stays the provider's count. The projection is recorded beside it as
`projected_tokens`, because "the provider said 195,503" and "we reckon it is 227,765 by now" are
different claims and the gap between them is the entire reason the pass fired.

### A refusal is read by pattern list, and an unreadable one says so

`_CONTEXT_LENGTH_PATTERNS` gains the OpenAI-shaped wording. The two state their numbers in
**opposite orders**, so both use named groups: read positionally, the new one says the window is
290,310 — the very size it was refused for — and the rung would compact against a window larger
than the one that exists.

A 400 that looks like a size refusal and that no pattern could parse is logged at warning with its
body. The next unknown wording then costs a log line instead of a game.

## Alternatives considered

**A per-seat growth margin.** Widen `headroom_needed` to the largest single-turn growth this seat
has shown, so the guard band is at least one stride. It works, and it is a workaround: it sizes a
margin to absorb growth we can already see exactly, and it would have been carrying the cost of not
looking. Rejected once it was clear the characters were counted on the line above the decision.

**A tokeniser.** What the comparable harnesses do — Anthropic's API answers `count_tokens` before
you send, and the OpenAI-family tools run `tiktoken` locally. Both count *the request in front of
them*, which is the property this ADR is really adopting. Neither is available here: OpenRouter
offers no counting endpoint, and the catalogue spans a hundred models across several tokeniser
families, so "a tokeniser" means the wrong one for most seats. A rate calibrated on this
conversation gets the property without the dependency.

**Leave it to the reactive rung.** The refusal carries exact numbers, so one could argue for
letting every over-large request fail once and recovering. It costs a wasted call per occurrence,
it depends on the rung being able to read the wording — which is the other half of this ADR, and
was false for two days — and a 400 that arrives while the ladder has nothing left to fold ends the
game. A backstop is not a plan.

## Consequences

`ed491262`'s seat folds at the turn it should have, and if the projection is still wrong the rung
that exists to catch it can now read the refusal.

**Nothing is capped and no model is judged differently.** A verbose model is untouched; what changes
is when we fold and how much output we ask for. `CONTEXT_EXCEEDED` and `ABANDONED` are harness
terminations already (ADR-0019, ADR-0031), so no rating moves — what these games cost was
measurements, not points.

**The projection is only as good as the transcript is homogeneous.** A chess transcript is board
dumps, SAN and move lists from one model on one endpoint, which is about the friendliest case there
is; a seat whose content changed character sharply mid-game would be projected badly. The `max` and
the reactive rung are what make that survivable rather than fatal, and `projected_tokens` in the
event log is what would make it visible.

**A related trap, recorded and not fixed here:** `alembic/env.py` calls `fileConfig`, whose default
is `disable_existing_loggers=True`. Any process that runs a migration in-process loses every
`chessmark.*` logger for the rest of its life — including the warning this ADR adds. It is why the
test for that warning re-enables the logger explicitly rather than trusting `caplog`. Production
migrates out of process, so the warning survives there.

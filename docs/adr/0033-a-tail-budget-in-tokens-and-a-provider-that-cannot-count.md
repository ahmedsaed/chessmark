# 0033. A tail budget in tokens, and a provider that cannot count

**Status:** Accepted
**Date:** 2026-09-08
**Amends:** [0021](0021-measured-windows-and-the-compaction-ladder.md) — `max_kept_messages`, which
bounded the wrong quantity. Extends [0019](0019-harness-bounds-are-not-findings.md) to an endpoint
whose *arithmetic* is broken rather than its availability.

## Context

ADR-0031 and ADR-0032 fixed how a turn grows and how a refusal is recovered from. Two games stayed
unrecoverable, and the reasons were different again.

### Twelve messages is not a size

`max_kept_messages = 12` bounds how much of the recent conversation survives a compaction. It was
introduced because "keep the last four turns" was unbounded, and it fixed that. It bounds the
transcript's *shape*, and the thing that matters is its *weight*:

| game | kept | characters | ≈ tokens of a 256,000 window |
| --- | ---: | ---: | ---: |
| `545dc41a` | 11 messages | 66,755 | ~23,000 |
| `10fc99f0` | 12 messages | **606,376** | **~210,000** |

Same cap, nine times the size. The larger filled 82% of its window with the one region compaction
is forbidden to touch — and that is the deadlock: to shrink the conversation the model must write a
summary, writing a summary needs room, and there is no room *because the conversation is too big*.
Every route out requires the thing that is blocked.

`10fc99f0` survived on luck. `29e7f004` and `e601f9af` did not, and sat abandoned through three
resumes each.

The reason the bound was in messages is that it was the only question answerable without an
estimate. A provider reports one token total for a whole request and never a figure per message, so
apportioning it across messages requires estimating, and this path had sworn off estimates
(AGENT-19).

### A provider that cannot count its own prompt

`29e7f004`'s last death was new: *"a 256000-token window holding a 549680-token prompt leaves
-297776 tokens to answer in"*. It never reached the provider — our own arithmetic refused first.

That figure came from `players.last_prompt_tokens`, persisted from the endpoint's reported usage.
And the endpoint's reports are impossible:

```
nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free   24 of 339 calls report a prompt
                                                      larger than the whole window
                                                      worst: 516,877 against 256,000
eleven other models, ~7,800 calls                     0
```

A call that **succeeded** necessarily fit. The receipt is wrong, and it is wrong in the direction
that wedges a game.

## Decision

**The retained region is bounded in tokens**, `KEEP_TAIL_TOKENS = 20,000` — the figure oh-my-pi,
Pydantic AI and Hermes all protect, arrived at for the reason we arrived at it: a session with
large tool results should protect fewer messages and a session of short exchanges more.

**Apportioned by a ratio measured on the conversation itself.** `players.last_prompt_characters` is
recorded beside `last_prompt_tokens`, both counted at the same instant on the same transcript, and
their quotient converts our exact character counts into that endpoint's tokens.

This is an estimate, and it is the right place for one. AGENT-19 governs whether a request **can be
sent** — being wrong there abandons a game. This decides how much history to **keep** — being wrong
keeps a slightly longer or shorter tail. Every comparable harness estimates here, with a generic
tokeniser; a ratio calibrated on this endpoint and this conversation is a better estimate than one
calibrated on English prose. A seat never measured has no ratio, and falls back to the message
ceiling rather than inventing one.

**A floor turn over budget is clamped, not kept whole.** When the one turn we must keep exceeds the
budget there is no legal cut left: dropping to zero leaves the model nothing to act on, and cutting
inside a turn orphans a `tool` result from the call that requested it, which every provider
refuses. So its largest messages keep their head and their tail and lose their middle, marked with
what went. Head and tail because a reasoning block opens with what it is considering and closes
with what it decided; the enumeration between is what the board can answer for (invariant 1).

Rendered from `content` on every replay rather than stored pre-cut, so the row serialises
identically each time and the cacheable prefix does not move (invariant 2). `content` is untouched,
like every other mark on that table.

**An impossible usage report abandons the game and names the endpoint.** Where `prompt + our
requested output` exceeds the context length on a call that *succeeded*, the report cannot be true.
There is no honest recovery: every model tokenises differently, OpenRouter exposes no counting
endpoint, and a local estimate is what AGENT-19 forbids — so the provider's numbers are the only
ones available and this provider's are not numbers.

**A stored impossible figure is discarded on the way in**, by the same test that refuses to store
one. A count is not made true by having been written down, and `29e7f004` carried 549,680 against a
256,000-token window through three resumes — large enough that every calculation concluded there
was no room, including the one deciding whether there was room to write the summary that would have
made room. Discarded, the seat is merely *unmeasured*, a state the harness already handles.

**Every rung that needs no provider survives a failed summarisation.** The trim already did; the
clamp was being dropped with the fold, which is precisely backwards — the clamp is the *only* rung
that helps when the single turn we must keep is itself over budget, and that is exactly the state a
game is in when its summary has no room to be written.

**A fold with no room to summarise proceeds without prose.** Discarding it was the right call for a
summarising call that *failed* — transient, worth leaving for a later pass — and the wrong one for a
call that could not be *made*. A transcript too large for the summary to fit is precisely the
transcript most in need of folding, and `e601f9af` sat at 254,103 tokens of a 256,000-token window
through four resumes because the one rung that could have rescued it needed the room it did not
have. The two cases are now distinguished, and the second folds with `SUMMARY_UNAVAILABLE` in place
of the model's own account — the loss stated rather than silent.

## Consequences

The deadlock is closed at both ends. The retained region can no longer grow to fill the window,
and when a single turn tries to, it is cut down rather than allowed to wedge the game.

**Requests get smaller for verbose models.** A reasoning model that kept twelve large messages now
keeps whatever fits in 20,000 tokens, usually two or three turns. That is less history than before,
and deliberately so: the board is authoritative and any tool can be called again (invariant 1), so
what is lost is recoverable in a way a stalled game is not.

**We now edit what a model said, in the request.** Clamping is the first mark that shortens a
message from within rather than dropping it whole. The record is unchanged — `content` and
`llm_calls` still hold every character — but a reader comparing the replayed prompt against the raw
payload will find the middle of a long reply missing, and the marker is what tells them why.

**A misreporting endpoint costs games rather than corrupting them.** Abandoning is the harsher
choice and the honest one: a game played on numbers nobody can trust is worth less than no game,
and an abandoned game scores against nobody (ADR-0019). If the endpoint is fixed the model returns
by itself; nothing here disables it.

The ratio inherits one weakness worth naming: it is a whole-conversation average applied to the
tail, and tool results are character-dense where prose is not. Being tens of percent wrong keeps a
tail of 14,000 or 26,000 tokens instead of 20,000, against a failure mode that was ten times over.

# 0021. The window is measured, and compaction trims before it summarises

**Status:** Accepted
**Date:** 2026-08-30
**Amends:** [0018](0018-context-compaction.md) — compaction gains a cheaper first rung, a
termination check, and a reactive fallback. Also amends [0019](0019-harness-bounds-are-not-findings.md):
a truncation caused by *our own* `max_tokens` never reaches the strike counter.

## Context

The first free-model pool finished 17 of 30 pairings. Two of the eleven abandonments were 400s, and
both were ours rather than the provider's. Chasing them found a single arithmetic mistake underneath
several unrelated-looking failures.

### The estimate was load-bearing, and it was wrong

`compaction.estimate_tokens` divides characters by 3.5. ADR-0018 says it is used "only before a
turn's first response, where nothing exact exists", and that was true of the design and false of the
code: `TurnRunner._prompt_tokens` lives on the runner, and the worker builds a new runner for every
turn. It was therefore 0 at the start of **every** turn, so the estimate — not the provider's count
— drove the first call of every turn, for the whole game.

How wrong it got is visible in the log. Game `e601f9af` recorded a compaction at
`occupied_tokens: 477155` against `context_tokens: 256000` — **at ply 6**. A six-ply transcript is
nowhere near a quarter of a million tokens. In game `29e7f004`, where a real count is available from
the refusal, the estimate said 292,113 where the endpoint counted 261,751.

### The estimate then forfeited a model

`Window.completion_cap` returns `max(1, min(requested, context - prompt - 256))`. Fed an estimate
larger than the window, the inner expression is negative and the floor takes over: **the endpoint
was asked for one output token.** Every response then came back `finish_reason: "length"`,
`_retry_truncated` counted four of them, and the game ended `truncated`, `1-0`, ranked, against
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at ply 5.

The floor's own comment — "a request for zero output is not a request" — is right about zero and
wrong about one. A `max_tokens` of 1 is not a smaller request; it is a request that cannot succeed,
and it converted a harness miscalculation into a finding about a player. That is exactly what
ADR-0019 exists to prevent, reappearing as arithmetic.

### Compaction could not converge, and reported success anyway

Game `29e7f004` compacted five times and was abandoned on a context-length 400 regardless:

| occupied | context | folded | kept |
| ---: | ---: | ---: | ---: |
| 292,113 | 256,000 | 26 | 29 |
| 239,875 | 256,000 | 7 | 26 |
| 302,916 | 256,000 | 3 | 41 |
| 438,913 | 256,000 | 17 | 41 |
| 415,490 | 256,000 | 4 | 50 |

`DEFAULT_KEEP_TURNS` is 4, and ADR-0018 sized that against "a turn is three to five messages". A
reasoning model that reads the board, enumerates moves, and retries a rejected move produces ten or
more — so four turns came to 41 and then 50 messages, and **the retained turns alone were larger
than the window.** Folding 3 messages of 44 freed nothing.

`Plan.worthwhile` is `bool(self.fold)`, so folding those 3 counted as success. `_compact` returned
True, the loop rebuilt its message list, believed it had made room, and sent a prompt that was still
6,254 tokens over. The once-per-turn guard then prevented any second attempt.

Nothing in the loop ever asked the only question that matters: **did that actually fit?**

### The bulk of a chess transcript is not worth summarising

`get_legal_moves` returns 38 or 39 move objects in a midgame position, and a turn calls it most
plies. By ply 55 the transcript is dominated by stale enumerations of positions that no longer
exist. Summarising them costs an LLM call to compress text whose value is already zero; the model
can regenerate any of it by calling the tool, which is authoritative (invariant 1).

The practice elsewhere agrees. Claude Code, Codex CLI and OpenCode all run a **cost-ordered ladder**
rather than a single summarise step: trim or snip tool results locally first, summarise only when
trimming is not enough, and catch `prompt_too_long` reactively as a last line of defence. Their
thresholds land at 85–90% of the effective window, which is where ADR-0018 already put ours.

### A transcript can hold a message no provider will accept

Separately, game `06916294` was abandoned on `{"code":400,"message":"Provider returned error",
"raw":"Assistant messages require \`content\`, \`tool_calls\`, or \`function_call\`",
"param":"messages.126.content"}` from Liquid.

`turn.py` appends an assistant row after every completion, unconditionally. A response with no
content and no tool calls — an ordinary truncation for `liquid/lfm-2.5-2.6b:free` — writes a row
with `content = NULL` and `tool_calls = NULL`, and `transcript.to_provider_message` renders it as a
bare `{"role": "assistant"}`.

The transcript is append-only (ADR-0003), so that row poisons **every later turn of that seat**.
This is the one 400 in the set that no amount of waiting or retrying could ever clear, which is why
it is worth naming separately from the context-length one: they share a status code and nothing
else.

## Decision

### The prompt size is always measured

**`estimate_tokens` is deleted.** Not improved — removed, along with `CHARS_PER_TOKEN`. Invariant 4
already says cost comes from returned token counts and never from an estimate; the same rule applies
to the arithmetic that decides whether a request can be sent at all.

The last measured `usage.prompt_tokens` is **persisted per seat** (`players.last_prompt_tokens`) and
seeds the runner, so every turn after a game's first call starts from a number the provider
returned.

Where no measurement exists yet — the first call of a game, and only there — we do not guess:

* **Compaction does not run.** There is nothing to fold at ply 1, so the decision is moot.
* **`completion_cap` uses a bound, not an estimate:** `min(requested, context // 2)`. A bound cannot
  be wrong in the dangerous direction. It keeps the clamp that AGENT-16 requires — a 65,536-token
  endpoint asked for 64,000 output tokens is the 400 that started all of this — without pretending
  to know a number we have not been told.

**`completion_cap` never returns an unusable value.** Below `MIN_USEFUL_COMPLETION` it raises
`NoRoomToAnswerError` rather than clamping. A number too small to answer in is a state to act on, not a
value to pass along.

### Compaction is a ladder, run in one pass

At the threshold — unchanged from ADR-0018 — one compaction pass does all of this at once:

1. **Bound the retained turns in messages as well as turns.** `keep_turns` stays 4, but whole turns
   are dropped from the front until the kept region is at most `max_kept_messages`, with a floor of
   one turn. This is the rung that fixes convergence: four turns *were* fifty messages.
2. **Trim stale tool results** inside the retained turns — every one but the newest turn's. They
   have been acted on, the board is authoritative, and a `get_legal_moves` dump of a position that
   no longer exists is the single largest thing in a chess transcript. Marked `trimmed_at` and
   replaced by a placeholder *in the request*; the row and its content are untouched.
3. **Summarise** everything before the cut, as ADR-0018 describes — but only if there is anything
   before the cut. A pass with nothing to fold still runs rungs 1 and 2, and costs no call at all.

**Trimming is not free, and that is why it is one pass.** Eliding a message rewrites the cacheable
prefix exactly as summarising does — invariant 2's named exception costs the same either way. What
trimming saves is the *API call*, not the cache miss, so doing the rungs separately would pay the
miss twice. Rung 1 is why the summary has less to carry and the kept turns are smaller, which is
what makes the pass converge.

A trimmed tool result is **elided, not removed**. Removing it would leave the assistant message
that requested it holding a `tool_call_id` with no answer, which every provider refuses — the same
family of mistake as the empty assistant message above.

**The pass does not predict whether it made enough room.** That would need a token count of part of
a transcript, which is an estimate, and there are none on this path any more. It is verified by the
next call's measurement, with the provider's refusal as the backstop.

**A compaction that did not make room is a failure, not a success.** `Plan.worthwhile` compares the
projected size against the window instead of asking whether anything was folded, and `_compact`
returns True only when the result actually fits. The once-per-turn guard becomes once-per-*pass*:
the pass may climb its rungs, and it may not restart.

### A context-length refusal is retried once, not abandoned

A 400 whose body names the context window becomes **reactive compaction**: the turn compacts with
the refusal's own numbers — which are exact, and better than anything we computed — and retries
once. Only if that fails is the request genuinely rejected.

This is the fallback that makes the first-call bound acceptable. Being wrong before the first
measurement is now recoverable rather than fatal.

### A ceiling we imposed is never a strike

**A truncation whose ceiling was our own `max_tokens` does not count toward the forfeit.** We know
what we asked for, so the two cases are distinguishable from the response, not merely arguable:

* our `max_tokens` bound the response → a harness failure. The turn fails and is retried; the strike
  counter is untouched.
* the provider's own ceiling bound below what we asked → a strike, as today.

`TRUNCATED` **stays in `RATED_TERMINATIONS`.** A model that cannot finish a turn inside a sane
budget has told us something true about itself, and with the window arithmetic fixed that is the
only way the termination can now be reached.

### A model that cannot hold one turn is never entered — and already was not

This was going to be new work, and reading the code first found it already there.
`registry.endpoint_is_playable` requires the **endpoint's** own window to clear
`min_context_tokens`, which is 64,000 — three orders of magnitude more than one turn — and all four
callers use it: `resolve_field` when admitting entrants, `select_endpoint` when pinning a seat,
`GET /models` when advertising, and the tournament field query. It was added when the
endpoint-versus-model window distinction was found, and it is strictly stronger than the rule
proposed here.

So nothing changes, and the property holds: a mid-game context failure means the model's *own*
output filled a window of at least 64k, which is a finding about the model and forfeits it honestly.
Recorded rather than quietly dropped, because "we should add X" and "X is already there, here is
where" are different facts and the second one is the useful one.

### An empty assistant message is never written

A completion with neither content nor tool calls appends **no transcript row**. There is nothing to
replay: the model said nothing and did nothing, and the two consecutive user messages that result
(the turn prompt, then the nudge) are accepted everywhere.

What the model returned is still recorded — the `llm_calls` row holds the raw response and the event
log holds the turn — so invariant 3 is untouched. What changes is the request.

### Compaction says what it did

The `compacted` event gains `trimmed`, `characters_before` and `characters_after`, and the frontend
renders the lot. Every number in the table above was already being written and none of it was on
screen; "folded 3, kept 41, freed nothing, still over" would have been legible the first time
instead of the eleventh.

**Characters, and labelled as characters.** The obvious field to add is "tokens after", and we
cannot have it: nobody has counted the new prompt yet, and inventing the number is the exact mistake
this ADR exists to remove. An exact count of a real quantity is worth more than an estimate of the
preferred one, and `occupied_tokens` — the provider's own count before the pass — is still there
beside it.

## Alternatives considered

**A better estimate.** A tokeniser per model family, or a larger safety factor. Both keep a guess on
the path where a wrong answer forfeits a player, and the exact number is already in our hands one
call later. Deleting the estimate is strictly better than calibrating it.

**Trim on every turn, not just at the threshold.** Cheaper in tokens and it would rewrite the
cacheable prefix every single turn, which is invariant 2 for no gain. Trimming saves a call, not a
cache miss; there is no reason to pay the miss before the threshold demands it.

**Drop `TRUNCATED` to `HARNESS_TERMINATIONS`.** Considered, on ADR-0019's own reasoning: a
provider's output ceiling measures the endpoint rather than the weights, and the same model on two
endpoints could get two verdicts. Rejected for now because with our own ceiling excluded, what
remains is a model that could not finish a turn — a reliability finding, and reliability is the
benchmark. Left as a live question rather than settled silently; see *Consequences*.

**Escalate compaction indefinitely, then judge whose bytes filled the window.** An earlier proposal
weighed the model's own output against our tool results to decide fault. It is measurable, and it is
machinery built for a case that the matchmaking check makes rare. Rejected as over-built.

**Rewrite the poisoned assistant row's content to `""`.** Simpler than superseding it, and it
gambles that every provider's check accepts an empty string when the one that refused us was
checking truthiness. Superseding uses the mechanism ADR-0018 already built for "stop sending this
row", and it cannot orphan a tool result because the row has no `tool_calls`.

## Consequences

**The first call of a game is bounded conservatively**, so a model with a small window may be
offered half of it for its first answer where it could have had more. It costs at most one
truncation on ply 1 and it cannot cost a game, which the alternative demonstrably could.

**A migration adds `players.last_prompt_tokens`.** A game in flight across the deploy has a null
there and simply falls back to the first-call bound for one turn, then measures.

**Trimming loses detail the summary never sees.** A trimmed `get_legal_moves` result is gone from
the request before any summariser reads it. That is the intent — the board is authoritative — but it
means a model cannot reason about a position it once enumerated without calling the tool again. The
count of trimmed messages is published in the event so this is visible rather than silent.

**`keep_turns` is now a ceiling rather than a promise.** A seat whose turns are enormous may be left
with one, which is a materially different condition from a seat that kept four. Recorded in the
event for the same reason the compaction count is published per seat (ADR-0018).

**Standings and ratings are still one decision, and human tournaments make two.** FIDE records a
forfeit as a loss in the crosstable and excludes it from the rating, because a tournament table must
be complete and a rating should only reflect games actually played. `db/tournaments.settle` and
`bench/ratable.judge` currently make the same call in both places, deliberately and with the
reasoning written down. Splitting them would give a third option for the truncation question above —
score it, do not rate it — and it is a decision of its own. Recorded in ROADMAP's *Known gaps*, not
taken here.

**Four frozensets still classify a termination** (ADR-0019), and this ADR does not add one.
`tests/bench/test_classification.py` continues to enforce their relationships.

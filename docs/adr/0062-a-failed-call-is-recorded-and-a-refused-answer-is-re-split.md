# 0062. A failed call is recorded, and a refused answer is asked again in smaller heats

**Status:** Accepted
**Date:** 2026-10-05
**Amends:** [0053](0053-every-failure-keeps-its-rounds.md), so that a failed turn now keeps
what failed as well as what was answered; and
[0059](0059-a-decision-model-with-a-limit-plays-in-heats.md), which gains a fallback for the host
refusing an answer. Implements LOG-01 for calls that did not answer.

## Context

Four `decision-cup` games were abandoned with the same message: `Tev1 answered "Kd1" for question
"heat_1"`, a 502. Three of them were the same position. The same opening reached it at ply 18, so
the same request was sent each time.

**The record could not say why.** A call that ended in an error left no row in either harness. The
gateway raised before anything was written, and a decision turn with no answered call was rolled
back whole. The request we sent and the provider's reply survived only as a truncated string in the
pairing's `abandoned_reason`. `llm_calls` has an `error` column that nothing had ever written. That
is invariant 3 ("log verbatim, never summarised") with a hole exactly where the evidence was needed.
The gateway's own comment said as much: *"a failure never reaches `llm_calls`."*

**So the failure was reproduced live instead**, by sending the production request again (about
$0.0002 in all):

- The same request was refused 12 times out of 12, with or without `session_id`, under any question
  name, and in any order of questions.
- `Kd1` *is* one of `heat_1`'s options. The host refused its own model's valid answer.
- Removing either `Kd1` or `Nd1` from the set made it answer. Smaller sets holding both were also
  answered.
- **Splitting the same 26 moves into three heats instead of two** (9/9/8) was answered, on this
  position and on the fourth game's.

The host's exact rule is not known from outside. What is known is that it is deterministic for a
given set of options, so the retries spent on it, four in the gateway and five job attempts, could
never succeed. Each was a wasted call, and the game ended abandoned, which is nobody's finding.

## Decision

**Every failed attempt is recorded, in both harnesses.**

- The gateways collect each failed attempt: the request (redacted, as every request is), the status
  code, the provider's body parsed into an object, the latency and the error.
- The attempts travel on the `LlmError` that is raised, and on the `Completion` or `Decision` when a
  retry then succeeded.
- The turn runners write each attempt as an `llm_calls` row:
  - `error` is set.
  - `response` is `{"status_code": …, "body": …}`.
  - The cost is zero, because a failure carries no usage. OpenRouter's own ledger is still
    reconciled against ours (ADR-0054), which finds any failure it did charge for.
  - `provider` is null, because the endpoint did not serve the call, and "served by" reads that
    column.
- A turn holding such rows is kept rather than rolled back (`INTERRUPTED`, ADR-0045). The resumed
  attempt numbers its calls after them.

**`llm_call_count` still counts answered calls only.** The chat loop seeds its round bound from it
on resume, and counting refusals there let a provider spend a model's rounds: eight rate-limit
pauses ended a turn as "out of rounds". That would be a harness bound becoming a finding about a
player (invariant 11). The failed rows are numbered in sequence and counted nowhere that limits
anything. The leaderboard's latency and a model page's call statistics read answered calls only.

**A host refusing its own answer is recognised, and not retried.** A 502 worded
`answered "X" for question "Q"` (with the quotes escaped, as they arrive inside the JSON body) is
raised at once, with `answer_rejected` naming the question.

**The turn is then asked again with one more heat.** This is the fallback found live. The per-
question cap is narrowed so the same moves split into one more heat, which changes every set.

- It runs at most `MAX_RESPLITS` (3) times, and never makes a heat smaller than three options.
- The whole turn is asked again, not just the refused heat. That keeps one rule for every round,
  and a refusal is rare enough that the heats already answered are a small price.
- The decision event records `resplits`, and the game page says "re-asked once after the host
  refused an answer". The heats a reader sees are then the second shape of the question.

**If every split is refused, the turn fails with that cause**, marked `request_rejected`. The same
position would ask the same questions again, so requeueing it is the waste this replaces.

## Alternatives considered

**Retry the identical request with a delay.** It was refused 12 times out of 12, including across
days.

**Treat the refusal as the model's fault and forfeit.** The model's answer was valid. The host
refused it, and charging a player for a host's bug is what invariant 11 exists to prevent.

**Re-split only the refused heat.** It saves a request, and makes the rounds' shape depend on which
heat failed. It is not worth the second rule for something that has happened in 4 games out of 57.

**Record failed calls in a table of their own.** They are calls: same request, same game, same turn,
same inspector. A second table is a second place for the turn inspector to look, and the column for
the error already existed.

## Consequences

- **Turns gain rows**, about one per failed attempt. Each is small, and a failure is uncommon. A
  turn that pauses repeatedly for rate limits shows every attempt in the inspector, which is the
  point.
- **The turn inspector already renders them.** `RawTranscript` has shown `error` and the raw
  response since it was built. It had simply never been given one.
- **The host bug should still be reported to OpenRouter.** The fallback works around it, and is
  bounded so that it cannot hide a host that refuses everything. The reproduction is kept with the
  report, not in the repository.

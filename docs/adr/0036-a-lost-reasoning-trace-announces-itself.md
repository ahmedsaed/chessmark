# 0036. A lost reasoning trace announces itself, so streaming can be on

**Status:** Accepted
**Date:** 2026-09-10
**Amends:** [0035](0035-live-frames-are-not-events.md), which shipped token streaming switched off
and made turning it on an operator decision per endpoint. Reinforces invariant 3 and
[0019](0019-harness-bounds-are-not-findings.md) — a provider library's bug is ours to detect, never
a finding about a model.

## Context

ADR-0035 built token streaming and refused to enable it. The reason was not caution about a new
feature; it was that the failure mode is **silent**.

LiteLLM's streaming path reads `reasoning_content` and discards `reasoning`, so on several
providers the model's thinking never arrives
([#21386](https://github.com/BerriAI/litellm/issues/21386),
[#20246](https://github.com/BerriAI/litellm/issues/20246)). An absent reasoning field looks exactly
like a model that does not reason. Nothing downstream can tell them apart: not the cost, not the
tool loop, not the leaderboard, not a person reading the page. The record would simply be missing
the most interesting thing in it, for some models and not others, with nothing anywhere saying so.

That left the feature real and unusable — *"turn it on once you have verified each model"* is a
sentence nobody executes, and the verification it asks for is a manual probe per endpoint that
goes stale with the next catalogue refresh.

**The failure is not actually silent, though.** It only looked that way because we were comparing
the reasoning text against nothing. There is a second, independent report of the same fact in every
response: `usage.completion_tokens_details.reasoning_tokens`, the provider's own count of the
tokens it spent thinking — the ones it *billed us for*.

    reasoning_tokens > 0  and  no reasoning text
        ⟹  the text existed, was paid for, and we did not collect it

That is not a heuristic. It is two statements from the same response contradicting each other.

## Decision

**Streaming is on, and an endpoint that contradicts itself is taken off it immediately.**

After each streamed call, a completion reporting billed reasoning tokens and carrying no reasoning
text takes its endpoint off the streaming path for the life of the process. The next call to that
endpoint is a whole response and its thinking is intact, so the cost of learning this is **one
call, per endpoint**.

**Keyed by endpoint, not by model.** The same weights served by two providers are two
implementations and only one of them may be losing the trace; keying by model would punish the
healthy one for its neighbour.

**A ratchet, and only while streaming.** Nothing re-enables an endpoint within a process. A
non-streamed call reporting the same shape is *not* evidence — it is the reference, and a model
that hides its reasoning legitimately reports exactly this.

**A model that hides its reasoning is taken off streaming too**, and that is the right trade. It
reports the same shape as a broken endpoint and cannot be distinguished from one without a second
call. The cost of being wrong is that one endpoint delivers its blocks a round at a time instead of
a token at a time. The cost of the other error is the benchmark's record.

## Consequences

`LLM_STREAM` stays, and now means "ask providers to stream at all" rather than "risk the record".
Turning it off is a way to reduce moving parts, not a safety measure.

The verdict is per process. A worker restart costs at most one more such call per affected
endpoint, which is a few hundred wasted reasoning tokens on a free model. The alternative — writing
a durable verdict about a bug in a library that may be fixed next week — would outlive the thing it
describes, and a stale "this endpoint cannot stream" is harder to notice than a warning repeated
after every deploy.

**A streamed call's stored `response` is reassembled, not verbatim.** `collect_stream` rebuilds the
shape a whole response would have had, and that reassembly is what `llm_calls` records. Invariant 3
is about not *summarising* — every token that arrived is in there, and `normalise_response` cannot
tell the two apart, which is asserted directly. But it is a real difference from a non-streamed
row, and anyone reading a raw payload from a streaming era should know the chunk boundaries are
gone.

The warning names the endpoint and the token count, so an operator sees which providers are
affected rather than being told that something, somewhere, is not streaming.

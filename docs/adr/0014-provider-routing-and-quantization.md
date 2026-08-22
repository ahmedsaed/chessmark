# 0014. Pin provider routing and exclude sub-8-bit quantization

**Status:** Accepted
**Date:** 2026-08-15

## Context

OpenRouter is a router, not a provider. One model id fans out across many independent hosts, and
they do not serve the same thing. `deepseek/deepseek-v4-flash` has 18 active endpoints: nine at
fp8, three at **fp4**, and six that decline to say. Left unconstrained, the router picks whichever
endpoint it likes on each call, by its own criteria.

That is fatal to a benchmark. Two games against "the same model" can be two materially different
contestants, and a leaderboard row silently averages them. Quantization is not a minor detail
here: 4-bit weights measurably degrade exactly the long-horizon, multi-step reasoning this project
exists to measure, and chess punishes it visibly. A rating computed over a mix of fp4 and fp8
serving is not a measurement of anything.

The complication is that the honest policy — "declare your precision, and it must be 8-bit or
better" — locks out the closed-weight models most worth benchmarking. Gemini reports `unknown`
from Google's own endpoints, and always will: there is no public weight file whose precision could
be disclosed. Excluding a model because its vendor has nothing to disclose would be a rule that
selects for open weights rather than for measurement quality.

## Decision

Every game carries a **`ProviderRouting` policy**, recorded on the game and resolved **per seat**,
and passed to OpenRouter as its `provider` block.

The default accepts `int8, fp8, mxfp8, fp16, bf16, fp32`. Every 4-bit format is excluded, and so
is undeclared precision — a provider that will not say what it serves could be serving anything.

**First-party widening** resolves the closed-weight problem. If a model's strict policy is
unsatisfiable — no endpoint meets it — and the model's own vendor is among its hosts, the policy
widens to admit `unknown` **and simultaneously pins `only` to that vendor's endpoints**. Gemini
becomes playable via Google and Google AI Studio; it does not become playable via a reseller that
happens to also report `unknown`. Widening never admits 4-bit under any circumstance, and a model
offered solely by undeclared third parties stays unplayable — the honest outcome is that it cannot
be benchmarked, not that we quietly accept it.

Resolution is **per seat, not per game**. A widening that pins `only` to one vendor is meaningless
for the other seat's model and, in practice, fatal: applying Gemini's `only: [Google, Google AI
Studio]` to a DeepSeek seat produced a hard 404.

Endpoints are synced into `model_endpoints`, and the precision that actually served each seat is
recorded per call and shown on the model card and in the game's stats rail.

## Alternatives considered

- **Leave routing unconstrained.** Simplest, and what most OpenRouter consumers do. It makes every
  number this project produces unattributable. Rejected outright — this is the one thing a
  benchmark cannot compromise on.
- **Pin every model to a single named provider.** Maximum reproducibility, but brittle: one
  provider outage blocks a model entirely, and choosing the provider becomes an editorial act we
  would have to defend per model.
- **Full precision only (fp16+).** Considered and kept available as `FULL_PRECISION_ONLY`, but not
  the default: fp8 serving is now standard for large models, and requiring fp16 would exclude most
  of the field for a difference far smaller than the fp4 cliff.
- **Accept `unknown` generally.** Would make Gemini work with no special case, but admits every
  undeclared endpoint including ones that are quietly 4-bit — which is the entire problem.

## Consequences

- A result can always state what it was played at. `players.provider_routing` records the policy;
  `llm_calls.provider` joined to `model_endpoints` records what served it.
- Some models are unplayable, visibly. The lobby marks them `unplayable` rather than hiding them,
  because "we could not benchmark this fairly" is itself a finding.
- Availability narrows. Constraining to fp8+ shrinks the endpoint pool, so throughput and latency
  are worse than unconstrained routing would give, and an outage among the qualifying endpoints
  can stall a model that would otherwise still run.
- The first-party list is a maintained trust assumption. It encodes "this vendor serves its own
  model honestly", which is a judgement, and it needs updating as vendors and aliases change.
- Verified in practice: an 80-ply benchmark game was served to DeepSeek by Baidu and StreamLake —
  both fp8 — with all three fp4 endpoints and all six undeclared ones excluded, while Gemini was
  served by Google under the widened policy.


---

## Amendment — 2026-08-23: precision was not the only thing an endpoint can get wrong

This ADR was written about quantization, on the reasoning that an endpoint serving fp4 is not the
model you meant to score. A later game found a second, sharper case: an endpoint that serves the
right weights at the right precision and still returns broken output.

`deepseek/deepseek-v4-pro` forfeited two games for "replying without calling a tool". It had in fact
called tools — it emitted them as raw DSML markup inside its reasoning instead of as structured
tool calls, which our runner correctly refused to act on. The obvious suspect was our own
transcript, which had been dropping `reasoning_details` and so replaying a history DeepSeek's
thinking mode considers invalid. That was a real bug and was fixed. **It was not this bug**; the
leak continued at the same rate afterwards.

Pinning the model to one endpoint at a time settled it:

| provider | calls | tool calls parsed | DSML leak |
| --- | --- | --- | --- |
| StreamLake | 63 | 54 | **9** |
| Baidu | 24 | 24 | 0 |
| DeepInfra | 16 | 16 | 0 |

Same model, same fp8 precision, same code, same opponent. StreamLake leaks on roughly one call in
seven; the other two endpoints did not leak once, and both played to the ply cap without forfeiting.
Under a shared 14% rate, zero failures in 40 calls has probability around 0.2%.

**The consequence for the benchmark is the uncomfortable part.** Our default routing sorts by
price, which is how StreamLake was chosen. Had those two games gone onto a leaderboard, they would
have read as "deepseek-v4-pro cannot call tools reliably" — a claim about a model, produced
entirely by an endpoint. Precision was never the only way an endpoint can change what a result
means.

Nothing about the policy changes yet, because the right response is not obvious: pinning every
model to one blessed endpoint trades one bias for another, and excluding an endpoint on one
model's evidence is too little to act on. What does follow immediately:

- `llm_calls.provider` is already recorded per call, so every existing result can be re-attributed.
  That was worth having and is now load-bearing rather than merely tidy.
- **A ranked result should not be produced by a single endpoint without saying so.** Whatever
  Phase 12 does about ratings, endpoint has to be visible next to the number.
- A harness-caused forfeit — `error_forfeit` from malformed tool calls — deserves separating from a
  model genuinely refusing to act, in the same way truncation was separated from refusal in Phase 5.


### Correction — 2026-08-23: the attribution above was too confident

The amendment concluded, from 9 leaks in 63 StreamLake calls against 0 in 40 across Baidu and
DeepInfra, that the DSML leak was the endpoint's fault. A later game leaked on **Alibaba**, which
that reasoning had not sampled at all:

| provider | calls | leaks | rate |
| --- | --- | --- | --- |
| StreamLake | 90 | 15 | 16.7% |
| Alibaba | 37 | 9 | 24.3% |
| Baidu | 24 | 0 | 0% |
| DeepInfra | 16 | 0 | 0% |

Two endpoints leak and two do not, so "the provider is at fault" is no longer supportable as
stated. The defensible version is narrower: **the model produces output that some endpoints parse
and others do not.** Whether Baidu and DeepInfra are genuinely clean or merely under-sampled is
unresolved — at a 20% rate, 24 and 16 calls would be expected to show around 5 and 3 leaks, so the
zeros are suggestive but not proof of immunity.

What does not change is the handling. Whoever is at fault, a model that emitted a tool call which
arrived as prose did not refuse to act, and forfeiting it publishes a claim its opponent did not
earn. ADR-0015 abandons the game either way, and that classification fired unrehearsed on the game
that produced these numbers: the turn was requeued, the model was not forfeited, and the game
carried on.

The lesson for the benchmark is about sample size rather than blame. Forty calls across two
endpoints looked decisive and was not. A claim of the form "endpoint X is unreliable" needs more
evidence than a claim of the form "these two results are not comparable".

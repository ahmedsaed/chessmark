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

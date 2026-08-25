# 0015. Quantization identifies the contestant; endpoints are pinned per match

**Status:** Accepted
**Date:** 2026-08-23
**Supersedes:** the exclusion policy in [0014](0014-provider-routing-and-quantization.md); its
recording and first-party-widening decisions stand.

## Context

ADR-0014 treated precision as a **filter**: sub-8-bit and undeclared endpoints were refused so that
a leaderboard row meant one thing. Two things since have shown that framing to be half right.

**It was solving the right problem the wrong way.** Excluding fp4 does keep it out of the average,
but it also throws away the comparison — "is this model worse at 4-bit?" is a question this project
is unusually well placed to answer, and the policy assumed the answer instead of measuring it. 4-bit
serving is also now common enough that refusing it excludes a real part of the field.

**And the filter did not actually pin anything.** Excluding precisions still leaves the router free
to choose among whatever remains, per call. Of the models with acceptable endpoints, 76 have more
than one and 40 have exactly one; for that majority the endpoint was never controlled. The first
paid benchmark — 80 plies, quoted repeatedly as a result about `deepseek-v4-flash` — was served by
**Baidu for 70 calls and StreamLake for 33, inside the same game**. That number is a measurement of
a blend the router picked, and re-running it would pick a different one.

Then the endpoint turned out to matter for correctness, not just precision: `deepseek-v4-pro` leaked
raw tool-call markup on 9 of 63 calls via StreamLake and 0 of 40 via Baidu and DeepInfra, same fp8
weights. Two games were lost by `error_forfeit` that would have been published as a claim about the
model.

## Decision

**A contestant is `(model, quantization)`.** Not a model. `deepseek-v4-pro@fp8` and
`deepseek-v4-pro@fp4` are two contestants, both playable, ranked separately and never averaged
together. No precision is banned; `unknown` is a value like any other, and a game served at an
undeclared precision says so.

**Every seat pins exactly one endpoint for the whole match.** Chosen by **uptime**, highest first,
among endpoints serving the requested quantization, with throughput as the tiebreak. When no
precision is requested, a **declared** precision is preferred over `unknown` before uptime is
considered. The choice is
resolved once at match creation, recorded on the player, and passed as `only: [provider]` for every
call in that game.

Uptime is a proxy. The intent is "the endpoint most likely to behave", and OpenRouter exposes no
request counts — uptime over the last day is the closest thing it does expose, and it measures the
property we actually care about more directly than popularity would.

**A provider's failure is not the model's failure.** Output that is malformed in a way the model
cannot be responsible for — tool-call markup delivered as prose, an endpoint returning a shape the
API does not define — abandons the game rather than forfeiting the player, exactly as a provider
outage already does. Chessmark has always separated "played badly" from "failed to operate"; this
adds "was failed by its host" to the second group.

**Floating aliases cannot be ranked.** `~vendor/model-latest` points at different weights over time,
so a rating computed across it is a rating of nothing. ~~They stay playable and stay excluded from
ranked play.~~

> **Amended 2026-08-25: they are not registered at all** (AGENT-14). Keeping them playable was half
> a decision. A rating across changing weights is a rating of nothing — but so is a *game record*
> that cannot say which weights played it, and BENCH-04 requires a run to record its model version.
> An alias game is therefore unreproducible whether or not anyone rates it, which is a worse
> failure for this project than being unplayable.
>
> The cost was near zero: of the 13 in the catalogue, 12 had no endpoint and could not be played
> anyway, one could, and none had ever been played. `is_floating_alias` survives as a predicate,
> because rows written before this rule still need identifying.

## Alternatives considered

- **Keep excluding sub-8-bit.** Simple and safe, and it silently answers a question we could be
  asking. It also does nothing about endpoint variance, which turned out to be the larger threat.
- **Average over endpoints.** What we were doing accidentally. It buries a provider defect inside a
  model's score, which is precisely the failure this ADR exists to prevent.
- **One endpoint per model, globally.** Reproducible, but an outage takes a model off the board
  entirely, and the choice becomes an editorial act to defend per model rather than a rule.
- **Cheapest endpoint.** The previous default, and how StreamLake was chosen. Price is a poor proxy
  for anything a benchmark cares about.
- **Make the endpoint part of the contestant's identity too.** Honest, and unusable: 857 endpoints
  across 229 models. Endpoint is recorded and shown, not ranked.

## Consequences

- The leaderboard gains a dimension. `model@fp8` and `model@fp4` are separate rows, which makes the
  precision question answerable instead of assumed.
- **Endpoint variance becomes a publishable finding rather than hidden noise.** That the same model
  is measurably less reliable through one host than another is a result worth having, and nobody
  else appears to be reporting it.
- Pinning removes mid-game endpoint switching, so a ranked result is reproducible by construction.
- A pinned endpoint that goes down takes its contestant with it for the length of the game. The
  reconciler already handles a stalled game; this makes that path more likely to be exercised.
- Uptime is measured by OpenRouter, so the selection inherits their measurement and its staleness.
  `model_endpoints` records what the numbers were when the choice was made.
- Every game before this ADR was played under an unpinned policy. Those results stay in the record
  and stay excluded from ratings; they measure a blend, and the blend is not reproducible.


## Addendum — what the interface says

The API and UI were still speaking the filter's vocabulary: `playable_quantizations`, a count of
models "blocked on precision". Both are gone.

- **`GET /models` returns `contestants`**, one per precision, each naming the endpoint that would
  actually be pinned and its uptime. The lobby reads *Contestants · 240 models · 365 entrants*, and
  a model with three precisions shows three rows rather than one row with two of them struck out.
- **`GET /games/{id}` reports `pinned_provider` per seat**, next to `providers_used`. They should be
  the same single name; when they are not, the seat is badged **MIXED ENDPOINTS** in red. The first
  paid benchmark now carries that badge, which is the right outcome — it says on its own page that
  it is not a clean measurement.
- **`POST /games` accepts `white_quantization` / `black_quantization`.** Asking for a precision
  nothing serves is a `400`, never a silent substitution: seating a different precision would
  measure a different contestant with no way for the caller to know.

**One field was deliberately not published.** OpenRouter's `supports_implicit_caching` is stored on
`model_endpoints` as a record of what the API said, and it does not predict behaviour — it reads
`false` for endpoints measured here at 91-94% cache hit rate (Azure/gpt-5.4-mini,
Baidu/deepseek-v4-flash, StreamLake/kimi-k2.5) and `true` for the one measured at 28%
(Google/gemini-3.7-flash). It was on the model card for about ten minutes before that comparison was
run. An interface element that is wrong more often than right is worse than an absent one.


## Amendment — 2026-08-23: a declared precision outranks `unknown` by default

Uptime alone was the first selection rule, and it had a consequence worth avoiding. `z-ai/glm-4.7`
is served by eight endpoints; the healthiest is **Google Vertex at `unknown`, 99.98%**, ahead of
Novita at fp8 (95.93%) and Z.AI's own fp4 (95.61%). So a game of "GLM-4.7" ran on a reseller at an
undeclared precision — recorded honestly, and not what anyone means by the name.

`unknown` now sorts last when no precision was asked for. It remains a contestant that can be
requested by name, and it still wins outright when it is all a model offers — a closed-weight model
has nothing to declare, and excluding it would be ADR-0014's exclusion policy returning by the back
door.

**The preference is declared-over-undeclared, not fp8-over-fp4.** Both are real contestants and
neither is inherently the right default, so uptime still decides between them: on current data
`glm-4.7` defaults to DeepInfra at fp4 (99.36%) rather than Novita at fp8 (95.93%). A consequence
worth stating plainly — **the default contestant for a model can change between games as uptimes
move.** That is survivable because the precision played is recorded and ratings group by
`(model, quantization)`, so two games at different precisions land in different rows rather than
being averaged. It does mean an unqualified model name is not a stable contestant, only a stable
*model*, and the game form shows which endpoint a choice will actually get before it is started.

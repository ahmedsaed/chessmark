# 0066. The person starting a game may choose its endpoint

**Status:** Accepted
**Date:** 2026-10-09
**Amends:** [0015](0015-quantization-as-identity-and-pinned-endpoints.md). An endpoint is still
pinned for the whole game; uptime now picks the default rather than deciding every time.

## Context

ADR-0015 pins one endpoint per seat, chosen by uptime with throughput as the tiebreak. The rule is
reasonable, but it gets the answer wrong often enough to matter. Uptime measures whether a host
answers, not whether it answers well. The case that started ADR-0015 shows this: StreamLake served
tool calls as prose on 9 of 63 calls, and its uptime would not have shown it.

`Seat.provider` could already force an endpoint, and it was used to tell a model's fault apart from
its host's. Only scripts could reach it. The play page let a person choose the model and the
precision, but not the host, so someone who knew one host played better had no way to say so. They
could only start the game and hope the uptime rule picked that host.

## Decision

**`POST /games` accepts `white_provider` / `black_provider`, and `POST /games/human` accepts
`model_provider`.** Leaving them out keeps ADR-0015's rule. Naming one pins that endpoint for the
whole game, and the name is recorded exactly as an uptime pin would be.

**A named endpoint must be one the uptime rule could have picked.** `select_endpoint` takes the
provider and applies the same `endpoint_is_playable` check: it must be active, call tools when the
model is a chat model, and have a window above the floor. It must also serve the requested
precision if one is named. Anything else is a `400`. Before this change a forced provider went into
`only` unchecked. A wrong or stale name from a form would have started a game that died at ply 0
with OpenRouter's 404, and the dead game would have read as a failure.

**`GET /models` lists every playable endpoint per contestant, healthiest first.** The first entry
is the one "auto" would pin. The list is filtered with the same predicate the pin uses, so the form
cannot offer a host the server would refuse. Before this change it filtered on `is_active` alone,
so a contestant could name a host whose window was under the floor. The list is part of the
catalogue the page already loads, so choosing a host never needs a second request.

**A ranked game may name its endpoint too.** The owner made this call. A contestant is
`(model, quantization)` and the endpoint is recorded, not ranked, so a chosen host rates in the
same row as an uptime-chosen one. `bench/ratable.judge` still requires the pinned endpoint to be
the one that served every call.

## Alternatives considered

- **Refuse a named endpoint on ranked games.** This would keep ranked hosts chosen by a rule rather
  than by whoever starts the game. It was turned down: games started from the site are unranked
  anyway, and the endpoint is not part of the contestant's identity.
- **Fetch a model's endpoints when it is picked.** This keeps `/models` small, but it puts a
  request and a wait between choosing a model and seeing its hosts, and needs a loading state the
  site does not otherwise have. The catalogue grows instead (below).
- **Change the default rule.** No better proxy is published. Whether a host plays well is what
  `providers_used` and the abandonment record measure over time, after the fact. A person can act
  on that knowledge sooner than a rule can be written from it.

## Consequences

- `/models` grew from 174 KB to 231 KB uncompressed for 295 models, and by about 9 KB gzipped. The
  whole catalogue is serialised into `/play`, so the cost is paid there. Latency is stored and left
  out of the response for that reason.
- **A ranked rating can now include games on hosts somebody chose.** Every seat's
  `pinned_provider` is published, so this is visible, but it is no longer the same as "the host the
  rule chose". If one host is ever pushed to sink or prop up a model, the response is to exclude
  named endpoints from ranked play. That is a change to the API, not to the data.
- A host shown in the form can go inactive before the game starts. The person then gets a sentence
  naming the host, not a game abandoned at ply 0.

# Providers, the catalogue, and what actually happens

Everything reaches models through **OpenRouter**, via LiteLLM, from the worker tier only
(invariant 10). This is what that has turned out to mean in practice — the parts that are not in any
provider's documentation.

## Who may enter the catalogue (AGENT-14)

Four kinds of model are **never registered**. Three cannot finish a game, and each failure would be
recorded as a *forfeit* — a loss against a model that never had a chance:

- **no tool calling** — the whole interface is tools;
- **`:batch` variants** — asynchronous, so there is no turn to wait for;
- **a context window below the floor** — see below.

The fourth is different: a **floating alias** (`-latest`, `~vendor/…`) plays fine but cannot say what
played, so its record is unreproducible (BENCH-04).
[ADR-0015](adr/0015-quantization-as-identity-and-pinned-endpoints.md) originally kept these
playable-but-unrankable, and was amended once it was clear that a game record which cannot name its
weights is as useless as a rating across them.

### The floor is 64k, and omitting it applies the policy

The transcript grows about **1,818 tokens per ply**, measured, so 128k covered roughly seventy plies
of a possible three hundred — and then forfeited. Compaction ([ADR-0018](adr/0018-context-compaction.md))
removed the reason for the higher number, so the floor is 64k.

`context_floor(None)` resolves to `settings.min_context_tokens`. It used to arrive as
`min_context: int = 0`, and `fits_a_game` reads 0 as *admit everything* — so a caller who simply
forgot the argument opted out of the rule. `refresh_catalogue.py` passed it; `seed_models.py` did
not. Now **omitting it applies the policy** and `0` is an opt-out somebody has to type.

### Three ways the catalogue used to disagree with itself

1. **A sync re-enabled what a sync had disabled.** `to_registry_entry` stamped `enabled: True` and
   the upsert wrote it onto existing rows, so `refresh-catalogue` disabled a sub-floor model and the
   next `seed-models` brought it back — and an administrator's deliberate disable did not survive
   either. **`enabled` is set on creation only.**
2. **The endpoint's own window was never read.** A model advertises a context length and an
   *endpoint* serves one; a 400 that abandoned a game said "**this endpoint's** maximum context
   length is 65536". Both numbers were already stored. `endpoint_is_playable()` is the single
   predicate shared by `select_endpoint`, `GET /models` and `resolve_field`, so the catalogue, the
   field and the picker cannot disagree.
3. **`GET /models` returned everything registered**, so the catalogue page advertised 18 models the
   picker correctly refused. It returns only models with an active tool-capable endpoint;
   `playable=false` still reaches the registry as stored.

`make prune-registry` / `./chessmark prune` applies the rule to the registry as it already stands —
reports by default, `--apply` to act. It **disables, never deletes** (`players.model_id` is
`ON DELETE RESTRICT`, and a game must stay readable however its model turned out), but it *does*
remove those models' games, which is the destructive half and the reason it reports first.

`--model <slug>` names one explicitly, bypassing the eligibility test: the rule is a prediction from
metadata, and a distribution gate is invisible to it (ADR-0019). Pair it with **`--only-named`**, or
`--apply` acts on everything in the report.

## Contestant identity

A contestant is **`(model, quantization)`**, so `model@fp4` and `model@fp8` are ranked separately
rather than one being banned. Every seat **pins one endpoint** for the whole game, chosen by uptime;
the router used to switch mid-game, and did.
[ADR-0015](adr/0015-quantization-as-identity-and-pinned-endpoints.md).

A provider's mangled output **abandons** the game rather than forfeiting the model.

**An endpoint can break a result without touching precision.** `deepseek-v4-pro` leaked raw DSML
markup instead of tool calls on 9 of 63 calls via StreamLake and 0 of 40 via Baidu and DeepInfra —
same model, same fp8. Provider is recorded per call for exactly this reason
([ADR-0014 amendment](adr/0014-provider-routing-and-quantization.md)).

## Request shape

Three things ride in **`extra_body`**, because all three are top-level OpenRouter body fields that
LiteLLM does not know by name and would otherwise drop:

| field | why |
| --- | --- |
| `usage: {include: true}` | cost from what the provider charged, not our multiplication (invariant 4) |
| `provider` | the pinned endpoint (ADR-0015) |
| `session_id` | `game-<uuid>`, derived and never stored |

`session_id` groups every call of one game into one conversation on OpenRouter's dashboard (LOG-08),
and it is also OpenRouter's **sticky routing key** — the provider-side version of what ADR-0015 pins
by hand. `agents/sessions.py` argues at length for both seats sharing one id.

**App attribution goes in headers, not the body.** `agents/attribution.py` sends `HTTP-Referer` and
`X-OpenRouter-Title` on every call carrying a real key — an app page, per-model analytics, usage
counted as ours. `APP_URL` falls back to the first CORS origin, which is the front end's own address
and therefore right on a development machine without a second variable to keep in step. They ride
only with a key, so a scripted gateway's recorded request stays byte-identical to its cassette.

**Reasoning must be handed back, not just recorded.** Gemini 3 rejects a function call missing its
`thought_signature`; DeepSeek rejects a thinking-mode history missing `reasoning_content`. OpenRouter
normalises both into `reasoning_details`, which `transcript_messages` stores and replays verbatim.
LiteLLM files it under `provider_specific_fields`, not at the top of the message.

**`max_completion_tokens` is clamped to the endpoint's window** (AGENT-16). Unclamped it was a flat
64,000 reconciled against nothing, which asked a 65,536-token endpoint for 65,810 tokens and was
refused — a 400 that abandoned a game at ply 10, and a failure the 64k floor would otherwise
recreate for every model in the new band.

## The free tier

**Free models can play.** A note in this repository said otherwise for a long time, and it was
measuring a bug: `fetch_endpoints` stripped the `:free` suffix before asking OpenRouter which
providers serve a model, on the belief that the endpoints route did not accept it. It does. A free
variant is served by an entirely different — usually *single* — provider from the paid one, so the
paid variant's 29 providers were stored against the free slug, the seat pinned the highest-uptime
one, and every free game died at ply 0 with a 404 naming a provider we had never chosen. Two tests
pin the path now, both verified to fail when the strip returns.

With that fixed, `poolside/laguna-s-2.1:free` vs `nvidia/nemotron-3.5-lightning:free` played 22
plies of the Giuoco Piano — five moves of correct theory, both sides castled, a real bishop sacrifice
on f7 — with **zero illegal attempts**.

What remains true is that free models are **slow and verbose**: 17s and 38s mean latency against
2.6s for paid models, worst call 442s, ~1,100–1,950 output tokens per call, and 2.95 calls per ply.
A worker plays one turn at a time, start to finish, so one of these blocks whatever is behind it —
`./chessmark workers 3` is the answer.

**The free tier is a shared pool, and it is patchy.** 429s carry
`limit_source: upstream_provider_shared_pool` and arrive from first-party providers too. A probe of
six free models returned four 200s, one 429 and one 403.

**A status code does not say whether the request or the endpoint is at fault — the body does.** 429,
403, provider-404 and a 400 whose body names endpoint health (`DEGRADED function cannot be invoked`)
are all *unavailability*: paused and cooled down. A 400 saying the completion does not fit the window
is our own arithmetic and fails fast. Three separate games were abandoned learning this one rule.

**A rate limit pauses the game; it does not retry it.**
[ADR-0017](adr/0017-rate-limits-pause-games.md) records that incident in full, including why three
of its four causes did not look like the problem. **A gate cannot be waited out at all** —
[ADR-0019](adr/0019-harness-bounds-are-not-findings.md).

## Results so far

| | result | plies | cost | illegal | cache |
| --- | --- | --- | --- | --- | --- |
| gemini-2.5-flash-lite vs deepseek-v4-flash | ½-½ `ply_cap` | 80 | $0.076 | 4 / 2 | 83% |
| gemini-3.7-flash vs kimi-k2.5 | **1-0 checkmate** | 39 | $0.124 | **0** / 5 | 73% |

Both sides played nine moves of correct Richter-Rauzer theory in the second. Across these games
**every** illegal attempt has been `not_reachable` — board-state tracking, never rule knowledge.

## Measuring time

**`game_events.created_at` is the *transaction* timestamp.** It defaults to `now()`, which in
Postgres is constant for a transaction, and a turn commits everything it produced in one (NFR-08) —
so a turn's `turn_started` and its `move_made` carry the identical instant.

Any timing derived from the log *within* a turn measures nothing, and "move landed → next turn
started" reads back the **previous** turn's duration, plausibly enough to be believed.

The reliable clocks are `latency_ms` on `turns` and `llm_calls`, both `perf_counter` in-process.
`./chessmark latency <game-id>` does the decomposition properly: provider, harness, and the queue
wait derived by subtraction.

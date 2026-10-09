# 0067. Reasoning effort is part of the contestant

**Status:** Accepted
**Date:** 2026-10-09
**Amends:** [0015](0015-quantization-as-identity-and-pinned-endpoints.md). A contestant becomes
`(model, quantization, effort)`, where it was `(model, quantization)`.

## Context

The harness has never set a reasoning effort. Every request carries the model, the messages, the
tools, `max_tokens`, the pinned endpoint and the usage flag. Nothing says how hard to think, so each
model reasons at its provider's default, and the game records neither the default nor the fact that
one was used.

That default is not a small knob. In game `5ec9cee5`, Claude Opus 5.5 played at OpenRouter's listed
default of **high** and GPT-6.1 Sol at **medium**. Opus averaged about 235 reasoning tokens a call
and GPT about 49. The game measured those two settings as much as it measured the two models. A
provider can change a default without notice, and a rating would then move with no version marking
it. That is the same hole ADR-0015 closed for precision, where the router's choice was a hidden
variable in every result. `docs/VISION.md` had already asked whether effort should be normalised
or reported as-is, and nothing answered it.

OpenRouter now publishes each model's reasoning metadata: whether reasoning is `mandatory`, whether
it is on by default, its `supported_efforts` and its `default_effort`. 336 of 469 models carry a
reasoning block. The unified `reasoning` parameter takes `{effort}`, `{max_tokens}` or
`{enabled: false}`, and OpenRouter translates it into each provider's own vocabulary.

## Decision

**A contestant is `(model, quantization, effort)`.** `opus-5.5@fp8@high` and `opus-5.5@fp8@low` are
different entrants, both playable and ranked apart, as `@fp8` and `@fp4` already are. Effort is
identity rather than configuration: what a model is asked to do is the same, but how much it may
think before answering changes the result, and a row that mixed efforts would be measuring the mix.

**The levels are OpenRouter's efforts, plus two of ours.** `none` is reasoning off. `auto` is
reasoning on with the effort left to the model, which is the only "on" for a model that reasons but
lists no efforts. The catalogue sync derives each model's levels and default from its `reasoning`
block (`agents/effort.levels_from_catalogue`) and stores them on `model_registry`, beside the block
itself. **`default_effort` does not mean reasoning is on.** It is the effort used when reasoning is
switched on without naming one, and 19 models are off by default while still naming it. So "on by
default" is read from `mandatory` and `default_enabled` alone, and an unstated `default_enabled` is
read as off.

**The effort is settled when the game is created, sent on every call, and recorded on the seat.**
The seat stores it in `players.sampling` (`{"model": ..., "effort": ...}`). That field exists to
record how the model was asked, and using it needs no migration. A seat that names no effort gets
the model's default at that moment, and it is still sent explicitly, so the record says what was
asked for even when nobody chose it. Each call reads the level from the seat, never from the
catalogue, so a default that moves mid-game does not change the game.

**`none` is a level.** For a model whose reasoning can be switched off, `none` sends
`reasoning: {enabled: false}` and is a contestant like any other. For a model that cannot reason at
all, `none` is the only level and nothing is sent, so its requests are unchanged.

**A model the catalogue has not described yet sends nothing.** That is a registry row from before
the migration, in the minutes before the refresh at deploy fills it in. Such a seat plays exactly
as every seat did before and records no level, and the same refresh labels it afterwards.

**Only levels the model lists are offered, and a level it does not list is refused.** Asking for
`high` from a model whose `supported_efforts` lacks it is a `400`, never the nearest level. Seating
a different effort would measure a different contestant, as ADR-0015 already says for precision.

**An endpoint must accept the parameter.** An endpoint whose `supported_parameters` lacks
`reasoning` would silently ignore the level. Every seat on a model that can reason sends one (its
default at least, and `none` is `enabled: false`), so such an endpoint can never serve that model.
The rule lives in `endpoint_is_playable`, so the catalogue, the form, a tournament's field and the
pin all agree. Unknown support (`NULL`, from rows before the column) is admitted.

**Old games are labelled from what they did.** A seat with no recorded level is labelled from its
recorded reasoning tokens, read through today's catalogue:

- A seat that reasoned gets its model's default if that is on. Otherwise it gets the effort the
  model uses when switched on.
- A seat that never reasoned gets `none` where the model offers it.

The label is marked `effort_inferred`, and the game page shows that. Ratings and pools stay
continuous: new default-level games land in the same row as the old ones, so nothing resets. The
labelling runs inside the catalogue refresh, straight after the sync that gives models their levels,
so it needs no operator step at deploy. It touches only seats with no level, and it deletes the
stored rating runs it has just made stale.

**Tournaments and pools get an effort setting.** The field filter gains `effort`, with the value
`default` or a level:

- **`default`** seats each entrant at its own model's default, resolved per game and recorded.
  Every existing event reads as `default`, so no pool changes behaviour or era.
- **A level** (for example, `medium`) seats every entrant at that level and **admits only models
  that list it**. A pool at `high` therefore has no model that cannot reason at high. Substituting
  the nearest level would give the event a field playing different tasks.

The setting is stored in the event's field filter, which a pool re-resolves every tick, so
entrants need no column of their own: every entrant in one event shares the level. It is fixed for
the event's life, and `set` cannot change it: an entrant whose level changed would be a different
contestant, so changing it means creating a new event. The matchmaker pairs each entrant on its
rating at the level it will play, never on another level's.

**Decision models are untouched.** They have no reasoning parameter (ADR-0049), so their key keeps
no effort.

## Alternatives considered

- **Report as-is.** Keep the provider defaults and record them. This is the cheapest option, but a
  rating would still move when a provider changed a default, and nobody could ask "does more
  thinking win more?".
- **One fixed effort for ranked play.** Simple and reproducible, but `medium` means something
  different to each provider. It also mixes a property of the harness into every model's result,
  where this ADR makes effort a property of the entrant.
- **Old games as a separate `@default` entrant.** Nothing would be inferred, but every current
  rating would restart and every pool would start over. Each model's history would also sit in a
  row that can never grow, beside a new row with no history.
- **A field that seats several efforts per model.** This is possible later on top of this ADR. It
  multiplies the field and its cost, so it is left until a single-effort pool shows the question is
  worth paying for.

## Consequences

- **The catalogue stores reasoning metadata.** For each model that means its `reasoning` block
  verbatim plus the derived `reasoning_levels` and `default_reasoning`; for each endpoint, whether
  it accepts `reasoning`. All columns are additive and nullable.
- **The leaderboard gains another dimension.** Rows are keyed `slug@quantization@effort`
  (`bench.service.contestant_label`; the web spells it the same in `lib/models.ts`). A decision
  model, and a seat never described, keep `slug@quantization`. Rows grow only where somebody
  chooses a non-default level, because events play at the default unless told otherwise.
  `RATING_METHOD` changed, so every stored run is rebuilt once.
- **Higher effort costs more, takes longer, and grows the transcript faster**, because prior
  reasoning is echoed back on every call (`agents/transcript.py`). Compaction (ADR-0018) arrives
  sooner. The call timeout is doubled for `xhigh` and `max`, which is where a hard position ran into
  600 seconds; a timeout still fails the turn rather than the model (ADR-0019).
- **Pre-5.x Claude gets a budget, not an effort.** For those models OpenRouter turns `effort` into
  `budget_tokens = max_tokens * ratio`. Our `max_tokens` is held back on a game's first, unmeasured
  call and shrinks as the window fills (ADR-0039), so the same effort would buy a different budget on
  different turns. For those models we send `reasoning.max_tokens` from the seat's stable completion
  ceiling, using OpenRouter's own ratios, and clamp it only when a call cannot hold it. Claude 5.x
  thinks adaptively, ignores a budget and maps `effort` directly, so it is sent the effort.
- **What "high" means is the provider's.** We record the level we asked for. Neither we nor the
  response can confirm what ran, so the per-call reasoning tokens stay the evidence.
- **An inferred label can be wrong** for a model whose default changed after its games were played.
  This is disclosed rather than corrected. Every game from this ADR on records its level, so the
  inference stops growing.

## Rollout

Shipped together (catalogue, seats, ratings, the API and forms, tournaments, timeouts and budgets)
rather than in the six steps this was proposed in, because each step on its own left a part of the
site saying something the rest did not.

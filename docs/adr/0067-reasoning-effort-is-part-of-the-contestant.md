# 0067. Reasoning effort is part of the contestant

**Status:** Proposed
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

**The effort is settled when the game is created, sent on every call, and recorded on the seat.**
The seat stores it in `players.sampling` (`{"model": ..., "effort": ...}`). That field exists to
record how the model was asked, and using it needs no migration. A seat that names no effort gets
the model's `default_effort` at that moment. It is still sent explicitly, so the record says what
was asked for even when nobody chose it.

**`none` is a level.** For a model whose reasoning can be switched off, `none` sends
`reasoning: {enabled: false}` and is a contestant like any other. For a model that cannot reason at
all, `none` is the only level and nothing is sent.

**Only levels the model lists are offered, and a level it does not list is refused.** Asking for
`high` from a model whose `supported_efforts` lacks it is a `400`, never the nearest level. Seating
a different effort would measure a different contestant, as ADR-0015 already says for precision.

**An endpoint must accept the parameter.** An endpoint whose `supported_parameters` lacks
`reasoning` would silently ignore an explicit effort, so it is not pinned for a seat that sends one.
It joins `endpoint_is_playable` for the seats that need it.

**Old games are labelled with an inferred default.** A seat with no recorded effort is read as its
model's current `default_effort` (or `none` where reasoning was optional and off by default), and
is marked `effort_inferred`. That keeps ratings and pools continuous: new default-effort games land
in the same row as the old ones, so nothing resets. The methodology page says the label is
inferred, and the reasoning tokens recorded on every call remain the evidence of what actually ran.

**Tournaments and pools get an effort setting.** The field filter gains `effort`, with the value
`default` or a level:

- **`default`** seats each entrant at its own model's default, resolved per game and recorded.
  Every existing event reads as `default`, so no pool changes behaviour or era.
- **A level** (for example, `medium`) seats every entrant at that level and **admits only models
  that list it**. A pool at `high` therefore has no model that cannot reason at high. Substituting
  the nearest level would give the event a field playing different tasks.

An entrant's effort is fixed for its event. `tournament_entrants` gains an `effort` column, which
is additive and nullable, with `NULL` meaning `default`. A running pool's setting is not
changeable: an entrant whose effort changed would be a different contestant, so changing it means
creating a new event.

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

- **The catalogue stores reasoning metadata** per model (`mandatory`, `default_enabled`,
  `supported_efforts`, `default_effort`), and whether each endpoint accepts `reasoning`. These are
  additive columns.
- **The leaderboard gains another dimension.** Rows are keyed `slug@quantization@effort`. Rows will
  grow only where somebody chooses a non-default effort, because pools play at the default unless
  told otherwise.
- **Higher effort costs more, takes longer, and grows the transcript faster**, because prior
  reasoning is echoed back on every call (`agents/transcript.py`). Compaction (ADR-0018) arrives
  sooner. A `max` turn can run into the 600-second call timeout, which under ADR-0019 fails the turn
  rather than forfeiting the model. The timeout should scale with effort rather than abandon those
  games.
- **Effort on a budget-based model drifts with `max_tokens`.** For Claude models before 5.x,
  OpenRouter turns `effort` into `budget_tokens = max_tokens × ratio`. Our `max_tokens` is
  recomputed every call as the window fills (ADR-0039), so the effective budget would shrink during
  a game. Those models either get a pinned `max_tokens` or are sent `reasoning.max_tokens` instead.
  Opus 5.x runs adaptive thinking and is not affected.
- **What "high" means is the provider's.** We record the level we asked for. Neither we nor the
  response can confirm what the provider ran, so the per-call reasoning tokens stay the evidence.
- **An inferred label can be wrong** for a model whose default changed after its games were played.
  This is disclosed rather than corrected. Every game from this ADR on records its effort, so the
  inference stops growing.

## Rollout

Each step ships on its own, and the ADR becomes Accepted with the first.

1. Catalogue: reasoning metadata per model; `reasoning` support per endpoint.
2. Seats: settle the effort, send it, and record it in `sampling`. From here every new game is
   reproducible.
3. Ratings: key on effort, and infer it for old seats.
4. API and forms: `white_effort` / `black_effort` / `model_effort`, and an effort row under the
   precision chips that defaults to the model's own default.
5. Tournaments: `effort` on the field filter and on entrants, plus `--effort` at `create`.
6. Effort-scaled call timeout, and the `max_tokens` fix for budget-based models.

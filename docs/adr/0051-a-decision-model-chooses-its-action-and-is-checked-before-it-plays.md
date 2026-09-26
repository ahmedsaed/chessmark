# 0051. A decision model chooses its action, and is checked before it plays

**Status:** Accepted
**Date:** 2026-09-27
**Amends:** [0049](0049-decision-models-play-through-their-own-harness.md): its yes/no gates, and its
assumption that every decision model answers the same questions. Bumps `DECISION_VERSION` to
**d2**.

## Context

Two findings, both from models arriving after ADR-0049 shipped.

### A gate needs a threshold, and a threshold does not carry between models

`d1` asked four `noul`s — resign, offer, accept, claim — and acted on each at 0.5, set by probing
Jev and Kev. A `noul` is an absolute probability, and each model uses the scale differently. In the
same dead-drawn ending, "should you offer a draw?" read **0.29 to Jev and 0.53 to Kev**; the clear
"no" positions sat at ≤ 0.07 for Jev and ≤ 0.33 for Kev. No single number serves both. OpenRouter's
own guidance says it outright: *"A threshold does not carry from a `noul` to a `choice` or from one
model to another."*

That left two options, and both are bad. A gate per model means probing every new model and
tuning each contestant's answers for it, a maintenance step and a quiet unfairness. One gate for
everyone decides some turns by how a model's scale happens to meet our number, which measures the
harness rather than the model. Three new decision models were listed in a day. Probing each was
not a step the owner would accept, and it should not have been needed.

### Not every decision model answers the questions a turn asks

Span-01 and Span-01 Lite (Respan) are listed as decision models with metadata identical to Jev's:
the same modality and an empty `supported_parameters`. Their host refuses a turn outright:

```
Respan state must be a string or an object with only input … and output …
Respan only accepts noul questions whose instructions and criteria are plain strings
```

Span-01 is a behaviour scorer. It answers yes/no over a conversation and never a `choice`, so it
cannot pick a move. Nothing in the catalogue says so. Paired blind, it is refused at ply 0 of every
game, and each refusal is an abandoned pairing. The Decision Cup was created with both Span models
seated before this was known, and they had to be withdrawn by hand.

## Decision

**What to do with the turn is one `choice`, and the option the model ranks first is what
happens.** The question is `action`, and its options are:

* `play_on`, always and first;
* `offer_draw`, unless the opponent's offer is open or this seat's last offer still stands declined;
* `resign`, always;
* `accept_draw` and `claim_draw`, when they are open.

The move stays its own `choice`, and "play on" means "play the move it ranked first". The actions
are mutually exclusive, which is what a `choice` is for, and a choice is relative: each model
decides on its own scale, with nothing of ours to tune or probe.

**With one rule: ending the game takes a majority.** Resigning, accepting a draw and claiming one
happen only when the model puts **more than half** of its action probability on that option.
Below that, the turn is played as its best non-ending action, and the event records what it ranked
first (`ranked_first`) so the rule is visible rather than hidden. This is not `d1`'s gate returning.
A `choice`'s probabilities sum to one, so "more than half" means "most of its belief" for every
model, and nothing is tuned per model. It exists because the first `d2` games had a plurality
resign a won game. Kev, in check with a free rook to take and Stockfish at **+7.7** for it,
resigned on 0.45 against 0.38 to play on. The asymmetry is the reason: playing on when a game is
lost costs time, since mate or the draw rules still end it, while resigning a game that is not lost
throws it away.

**A decision model is checked once before it is offered.** `agents/decision_check.py` asks every
decision model not yet checked under the current `DECISION_VERSION` one tiny request in exactly the
shape a turn sends: a two-ply position, the move `choice` and the action `choice`. The answer goes
on the registry row. `decisions_checked` records the version it answered under, and
`decisions_refusal` records the host's own message when it refused.

* Only a model that answered under the current version is playable: in tournament fields, the
  catalogue, the picker, and both game-creation endpoints.
* A refusal (a 400, or an answer that is not an answer) is recorded and permanent for that version.
  A failure about the moment (a rate limit, an outage, an empty account) records nothing and is
  asked again on the next refresh.
* It runs in the catalogue refresh, so a new model is checked within one refresh with no operator
  step. It runs once per model per version, never on routine refreshes. A refused request is not
  billed, and an answered one costs a few hundred input tokens.
* This is a capability check, not calibration. With no gates there is nothing left to calibrate,
  and `make probe-decisions` becomes a diagnostic rather than a step.

**Only `:free` models are free.** Span-01 Lite is priced at zero with no `:free` suffix, and it
stays paid. Whether a model is free is the slug's statement, as it has always been.

## Alternatives considered

* **Keep the gates and probe each new model.** Rejected by the owner for the maintenance, and it
  meant the harness tuning each contestant.
* **Pure plurality with no majority rule.** It was tried, and Kev resigned a position Stockfish had
  at +7.7.
* **Put the actions into the move `choice`.** A model's move probabilities are spread across 20–40
  moves; Kev's top move often held under 10%. "Resign" at 0.12 would outrank every move, so the
  comparison would be resigning against one move rather than against playing on. An offer cannot be
  one option there at all without doubling every move. Two judgements, two questions.
* **Learn incompatibility from the first refusal in a game.** It makes no extra calls, but it costs
  a pairing slot and leaves an abandoned game in the record, once per incompatible model. The check
  up front costs a fraction of a cent, once.

## Consequences

* **`d2` is a new era.** Decision games under `d1` stay in the record and out of `d2` ratings, and a
  decision pool replays its pairs up to its target.
* **Two real games per rule, scored by Stockfish.** Under plurality, both resignations came on less
  than a majority (0.42 and 0.45), one of them in a winning position. Under the majority rule, the
  same won position was not resigned. And a lost position was played on to a threefold draw because
  the winning side never converted, which is the rule's stated cost and the opponent's failure.
  Neither `d2` game drew an offer in a level position; `d1` games made 8 to 28 offers each.
* **The probe, reworded as a diagnostic, found both models choosing a sound action in 9 of 12
  labelled positions.** Both resign, rather than accept or claim, a draw when badly lost. That is
  their judgement, a half point they give away, and nothing to tune.
* **A decision model is unplayable from deploy until the catalogue refreshes.** `./chessmark deploy`
  now restarts `catalogue`, which refreshes at start-up. It had never been restarted by a deploy.

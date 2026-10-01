# 0059. A decision model that cannot take every move at once plays in heats

**Status:** Accepted
**Date:** 2026-10-01
**Amends:** [0051](0051-a-decision-model-chooses-its-action-and-is-checked-before-it-plays.md): its
check, which asked a two-ply opening and learned only whether a model answers. Bumps
`DECISION_VERSION` to **d2.1**, a minor version.

## Context

Two decision models were listed after `d2` shipped, and the check refused both:

```
togethercomputer/tev1-4b-experimental
  Tev1 needs between 2 and 20 options per question; question "move" has 29
upstage/solar-decide
  question move: 29 candidates exceed the 26 single-token labels this route supports
```

The check was right to refuse them. A middlegame has 30 to 40 legal moves, and `d2` puts every one
of them in a single `choice`, so in real games both models would fail most turns. But this is a
limit of the model's interface, not a judgement about its chess, and nothing in the catalogue
states it. The question was whether such a model could be let in without changing what any other
model is asked.

Tev also takes **at least two** options. `d2` asked the move question even when only one move was
legal, so Tev would have been refused on every forced move as well.

## Decision

**A model with a limit is asked its move in heats, and then in a final.** The legal moves are split
into as few heats of at most its limit as will hold them, as even in size as they can be (34 moves
under a limit of 20 make two heats of 17, not 20 and 14). Each heat is one `choice` question
(`heat_1`, `heat_2`, …), all in one request, and the action question rides along with them. The heat
winners then meet in a final, asked as the `move` question in a second request. If there are more
winners than the limit allows, they play another round of heats first, so any position fits any
limit the check records. `agents/decision_rounds.py` runs this.

**A model with no limit is asked exactly what `d2` asked it, byte for byte.** The recorded request
for `d2.1` is identical to `d2`'s (`tests/fixtures/decision_requests/`). Heats apply only to a model
whose own limit is below the number of legal moves.

**The limit is the model's, found by the check and stored on its row**
(`model_registry.decisions_max_choices`). The check asks over the position with the most legal
moves chess has: 218, eight white queens and the rest, reached by a Black move so that every field
a turn sends is present. A model that answers has no limit worth knowing (`NULL`). One that is
refused is asked again with each question **halved** — 218, 109, 54, 27, 13, 6, at most six
requests — and the largest size it answered is recorded. Each smaller request is shaped as the
first round of heats a turn under that limit would send, with **every** heat at exactly that size,
so a single request proves both the size of a question and the number of questions in one request.
A model refused even at six is refused, with its host's own words, as before. A rate limit or an
outage part-way down the ladder records nothing, and the next refresh asks again.

**A forced move asks only the action question.** Its answer is settled, a question with one option
tells the model nothing, and a host that needs two would refuse it. The event says `forced: true`.

**This is a minor version.** `same_task` treats `d2.1` as the same task as `d2`. A model with no
limit plays exactly the game it played before, so Jev's and Kev's ratings and the decision pool's
era carry on. For a model with a limit, heats are how it is asked at all. The handicap is real, it
falls only on the model that has the limit, and it is part of what the model is. Compaction already
works this way for chat models ([ADR-0018](0018-context-compaction.md)): the rule is fixed and
versioned, and the point at which it applies is a property of the model.

**What is recorded.** Each request is its own `llm_calls` row, in sequence, and the turn's spend is
all of them (invariant 4). The `decided` event keeps `probabilities` as the deciding question's
distribution: every legal move without heats, the finalists with them. It adds `heats`, each one's
round, winner and whole distribution, which is withheld from a person mid-game like the rest of the
answer (invariant 8). A turn that fails after a heat was answered keeps that call, as a chat turn
keeps its rounds ([ADR-0053](0053-every-failure-keeps-its-rounds.md)). The turn is `INTERRUPTED`,
the retry asks again from the start on the same row, and its calls continue the sequence.

## Alternatives considered

**Heats for every model** (`d3`). This is what fairness first suggested: one shape of question for
everyone. It lost because it changes what Jev and Kev are asked in order to accommodate a limit
they do not have. It splits the pool's era and costs every unlimited model a second call per turn,
and a heat can knock out a strong move before it reaches the final.

**The piece, then the square.** Asking which piece to move and then where reads naturally, but it
does not fit. A queen in the open has up to 27 moves, which is over Tev's 20, so it would still need
heats. And it asks the model to commit to a piece before seeing what that piece can do.

**Parsing the limit out of the refusal.** Tev and Solar both state their limits, in different
words. A pattern for each host would be wrong the first time a third host phrases it differently.
Halving finds a working size without reading anyone's prose. The cost is precision: Tev takes 20
and is recorded at 13, so a 39-move position is three heats where two would have done. That is a
fraction of a cent per turn.

**A binary search for the exact limit.** It would record 20 for Tev, but it costs more requests per
model and the precision buys little. The owner set six requests as the budget.

## Consequences

- Tev and Solar are playable and play in heats, recorded at 13 options each. A heats turn is two
  requests, about $0.0002. Jev and Kev, checked again under `d2.1`, answered all 218 moves in one
  question, so their turns are unchanged.
- Every decision model is re-checked once when `d2.1` is deployed, because the version changed.
  Until the catalogue refresh that runs at deploy finishes, no decision model is playable.
- A heat can eliminate the move a single question would have chosen. That cost falls only on a
  model with a limit, and the record says when a move came from a final (`heats` in the event, and
  the turn's own line on the game page).
- Respan is still refused. Its host takes only yes/no questions, and asking one per legal move
  would be a different task, not a limit worked around.

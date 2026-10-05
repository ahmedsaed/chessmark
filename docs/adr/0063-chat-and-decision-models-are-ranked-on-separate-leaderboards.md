# 0063. Chat and decision models are ranked on separate leaderboards

**Status:** Accepted
**Date:** 2026-10-05
**Amends:** [0049](0049-decision-models-play-through-their-own-harness.md). It decided that
decision models get their own tournaments but share the global leaderboard; they now have a
leaderboard of their own. Rewrites BENCH-13.

## Context

ADR-0049 put decision models on the same leaderboard as chat models. They never played each other:
the two groups' games are disconnected, so their places relative to each other came from the shared
1500 starting point and from no game at all (ADR-0060 recorded this as a known gap).

Two ways to tie the groups together were considered, and both were set aside:

- **A bridge event between the groups.** A one-sided bridge measures nothing: if one group wins every
  game, the fit can only say "far apart", and the prior decides how far. The two harnesses also do
  not play the same game. A decision model is handed the legal moves and facts about each and can
  never play an illegal move. A chat model must find a legal move itself and forfeits after five
  failures, so a cross-group result mixes chess with rule-following.
- **Anchor bots** played by both groups. Before relying on them, the decision models were checked
  against the anchors themselves (`scripts/anchor_trial.py`, bar fixed before the run: beat a random
  mover with the 95% interval's lower bound above 50%).

**The anchor trial.** The top four of `decision-cup` (Clef, D1, Clef-flash, Jev-1.13) played 5
games against each bot on 2026-10-05, through the production decision path:

| Model | vs random W/D/L | vs greedy W/D/L |
|---|---|---|
| D1 | 4/1/0 | 0/2/3 |
| Jev-1.13 | 2/3/0 | 1/0/4 |
| Clef-flash | 1/4/0 | 0/3/2 |
| Clef | 0/5/0 | 1/3/1 |

None passed. They are not random movers: against the random bot each won material in all twenty
games, reaching 20 to 50 points ahead. What they could not do was finish a won game. Ten of the
thirteen draws ended with the model 5 or more points up: Clef claimed threefold repetition four times
while 19 to 37 points ahead, and others stalemated the bot or traded down to insufficient material.
Against the greedy bot, which takes any piece left unprotected, they mostly lost. The action
question tells them their material standing and says to draw only when clearly worse, so the claims
were their own.

## Decision

**Chat models and decision models are ranked on separate leaderboards.**

- **One stored run, two views.** The groups are already disconnected, and with each contestant's
  own prior a fit over two disconnected groups is exactly two independent fits. So no rating changes
  and no second stored run is needed. The page chooses which rows to show and numbers places within
  that group.
- **`/leaderboard` shows chat models by default.** Decision models are one control away
  (`?models=decision`). The control is a pair of links, so the choice lives in the address, as the
  archive's filters do, and needs no JavaScript.
- **The decision view says why it is separate**, in one paragraph beside the table, with the trial's
  result.
- **The landing page and the leaderboard's social card show chat models only.** So does the landing
  page's "know your opponent" panel. It counted decision models as having "never tried an illegal
  move", which they cannot do, so its clean share was partly a fact about their harness.

**Games between a chat model and a decision model are decided separately**, and are not covered
here. None has been played yet, so nothing changes for them today.

## Alternatives considered

**Two tables on one page.** Rejected by the owner in favour of a control. One table at a time keeps
"#1" meaning one thing on the screen.

**A bridge event, or anchors as the bridge.** Set aside for the reasons above. The trial is what
settled it: a scale linking the groups through games would rank a skill the decision models do not
show (converting a won game) against one the chat models are measured on (following the rules
unaided).

## Consequences

- **Model pages and the archive still mark decision models** (BENCH-13). Only the ranking is split.
- **New decision models are checked the same way:** `make anchor-trial` can be rerun on them, with
  the same bar.
- **If decision models later pass the bar**, linking the groups can be reconsidered with evidence
  rather than assumed.

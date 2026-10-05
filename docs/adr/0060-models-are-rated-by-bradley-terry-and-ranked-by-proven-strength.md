# 0060. Models are rated by Bradley-Terry, and ranked by the strength they have proven

**Status:** Accepted
**Date:** 2026-10-05
**Supersedes:** [0028](0028-a-wider-prior-and-a-provisional-mark.md)'s prior (its provisional mark
stands) and [0029](0029-a-deviation-has-a-ceiling.md).
**Amends:** [0027](0027-a-pool-is-ranked-by-its-own-rating.md). A pool is still ranked by a rating
over its own games, but that rating is a different model, and it is ordered by a different key.
Rewrites BENCH-01.

## Context

`pool-free`'s table put Qwen3.8-27B, at 10 wins and 2 losses, third. Above it were two models on
3/0/0, one of which had left the field and will not play again. A reader asked why. The answer led
to the rating system itself, not just the sort.

**Glicko-2 models players whose strength drifts over time.** Rating periods, a deviation that
widens while a player is idle, and volatility all exist for a human whose form changes between
Tuesday and Friday. A model is a fixed set of weights and does not drift. A paid model may play six
games in one event and never return. For this population, Glicko-2's time machinery measures
something that does not happen, and it has two visible consequences:

- **The ranking depends on the order games were played in.** `scripts/compare_ratings.py` deals
  the same 116 production games into the same days in 300 different orders. Dots3-Note placed
  anywhere from 1st to 13th, Ling-Fin from 3rd to 17th and Laguna-S from 4th to 21st, all from
  identical results.
- **A model that stops playing is judged by the calendar.** Its deviation grows on every day other
  models play, though nothing new is known about it.

**A short perfect record has no finite rating without a prior**, and Glicko-2's was large and
unstated: ADR-0028's starting deviation of 500. That is why a 3/0/0 streak topped the table. Any
rating system has to state how much it trusts a short record. The choice is whether to say so.

**A best guess alone cannot express what readers mean.** Even with the prior below, the best
estimates for Nex-N2.5-Pro (3/0/0) and Qwen (10/0/2) are 1781 ± 209 and 1743 ± 134: 38 points
apart, a fraction of either ±. No
prior makes "10/12 is better" true as a best guess. What a reader means by it is that Qwen has
**proven more**.

## Decision

**Ratings are Bradley-Terry: one fit over every counted game at once, with no dates.**

- The chance that A beats B is `1 / (1 + 10^((B − A) / 400))`, which is Elo's scale, so the
  numbers read the way chess ratings do.
- A draw is half a win, as before.
- The same games give the same ratings in any order. A retired model's rating does not change
  because time passed.
- It changes only through re-evaluation: if the models it beat later prove stronger, beating them
  was worth more, and the other way round.

**Every model starts with two draws against an imaginary 1500-rated opponent.** That is the prior,
stated in a form a reader can check, the same trick IMDb uses so three 10/10 votes cannot outrank
an established film.

- Two was chosen from the data, not by taste. The comparison script estimated the prior's width
  on `pool-free` two independent ways: marginal likelihood, checked against numerical
  integration, and leave-one-out prediction. They put it at 225–400 rating points, which is one to
  two imaginary draws. Two is the more cautious end.
- The imaginary games are part of the fit, not a separate correction, so the published rating is
  exactly the rating those games produce.

**The ± is the fit's own uncertainty** (Laplace: the inverse of the curvature at the best fit),
reported in rating points.

- A model with no games has ± 246, which is what two imaginary draws are worth.
- The ± shrinks only with games. It never grows, because nothing in the model involves time.
- ADR-0029's ceiling therefore has nothing left to cap.

**Tables are ordered by proven strength, `rating − 2 × ±`, on the leaderboard and in a pool.**
This is the rating a model has shown it is at least, at roughly 95% confidence.

- More games at the same rate rank higher: 10/0/0 above 6/0/0 above 3/0/0.
- More wins at a worse rate do not. A 25/0/25 record does not pass 10/0/2.
- A retired model keeps its proven strength and does not gain more. Models that keep winning
  overtake it as they prove more.
- The rating and its ± are what each row displays. The order is explained on the page in one
  sentence, because a table sorted by a number it does not show has to say what it is sorted by.
- Ties on proven strength fall back to the rating, then the key, so the order is total and
  deterministic.

**ADR-0028's provisional mark stands**: a `?` while the ± exceeds 110. It is still a caveat, not a
penalty. Under this ordering the ± already moves the row, so the mark explains a position rather
than adding a second rule.

**The stored leaderboard records which rating method produced it** (ADR-0032). A row computed
by Glicko-2 fails the fingerprint after deploy and is rebuilt on the next read, rather than being
served as if it were current.

**The matchmaker is unchanged in policy.** It still needs a strength and a confidence, and gets
them from the new fit.

## Alternatives considered

**Keep Glicko-2 and only change the sort.** This would fix the symptom that was asked about and
keep the order-dependence and the calendar-driven deviation. The order-dependence is the stronger
objection: a benchmark whose places move when the same results are reshuffled is not measuring the
models.

**Bradley-Terry ordered by the rating alone.** It is honest, and it leaves 3/0/0 above 10/0/2 by
38 points that mean nothing next to a ± of 209. The question that started this would get the same answer.

**Estimate the prior's width on every rebuild.** This is more principled in theory. In practice,
on 65 games the evidence is nearly flat from 200 to 400, so the published numbers would move with
noise in the estimate. And "two imaginary draws" stops being a sentence a reader can be given. It
is revisited if the pools grow by an order of magnitude.

**Order by points, as a closed event does.** This is right when everyone plays the same schedule
and wrong in a pool, for ADR-0027's reasons, which still hold.

## Consequences

- **Every rating on the site changes once**, on the first read after deploy. Most move by tens of
  points, and short records move the most. In `pool-free` the two 3/0/0 rows drop from first and
  second to third and fifth, and Qwen (10/0/2) goes from third to first.
- **`volatility` disappears** from the stored run and the API. It was a Glicko-2 parameter with no
  equivalent here.
- **Rating periods are gone.** `periods` disappears from the stored run, and the date a game ended
  stops mattering to its rating.
- **A rebuild is a fit, not a replay**: Newton's method over a matrix the size of the field. At 25
  contestants it is a fraction of a millisecond. The cost grows with the square of the field, and
  that would only matter in the hundreds.
- **Decision models are rated by the same rule** and are not otherwise considered here. They have
  never played an LLM, so the two groups' places relative to each other come from the shared
  starting point, not from any game. That was equally true under Glicko-2. It is recorded in
  ROADMAP's known gaps, to be settled when decision models are revisited.
- **Two imaginary draws is a number we now own.** If a reader asks "why two?", the answer is the
  comparison in `scripts/compare_ratings.py`, kept in the repository for that reason.

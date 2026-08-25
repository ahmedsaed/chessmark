# 0016. Credits are a granted balance, priced per model

**Status:** Accepted
**Date:** 2026-08-25
**Supersedes:** layer 2 of [0011](0011-server-keys-layered-budgets.md) — the per-user *daily*
quota. Layers 1, 3 and 4 stand unchanged.

## Context

ADR-0011 gave each user a daily allowance: 20 games and $5, regenerating at UTC midnight. It was
the right shape for an open site nobody had opened yet. Three things have changed.

**The word "credit" was a UI invention with nothing behind it.** It appears exactly once in the
codebase — a comment in `AccountBar.tsx` — rendering `games_remaining_today`. The backend has no
such concept, no requirement mentions it, and no document defines it. Anyone reading the header
would reasonably assume they hold something; they hold a countdown that resets whether they use it
or not.

**A daily reset is the wrong control for a private testing phase.** Chessmark is not open. The
owner needs to decide who plays and how much, and a quota that refills every midnight grants
access continuously to anyone who has ever signed in. Twenty games a day to an unattended account
is twenty games a day forever.

**One game is not one price.** The catalogue is 330 playable models spanning **$0.09 to $30 per
million input tokens** — a 300-fold range, and output prices reach $180/M. Charging one credit for
any of them means a `gpt-5.5-pro` game and a `minimax-m3` game cost a user the same and cost us
two orders of magnitude apart. Before this was measured the registry held 239 stale entries topping
out at $1/M, which had hidden the spread entirely.

## Decision

A credit is **a unit of granted play, held as a balance, spent to start a game.** It does not
regenerate. New accounts hold **zero**; an administrator grants them.

**A model has a credit price, in four tiers, set by whichever of its two prices is worse.**

| Tier | Credits | Input ≤ | Output ≤ | Models | Share |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | $0.30/M | $1.50/M | 129 | 39.1% |
| 2 | 2 | $2.00/M | $8.00/M | 129 | 39.1% |
| 3 | 3 | $10.00/M | $40.00/M | 55 | 16.7% |
| 4 | 6 | above | above | 17 | 5.2% |

A model qualifies for a tier only if **both** prices fit it. A model that is cheap to prompt and
ruinous to generate is still a model that can hurt, and the failure is asymmetric: mispricing one
downwards costs real money, mispricing it upwards costs a user one credit.

The boundaries sit on real clusters — $0.30/$1.20 and $2.00/$6.00 are prices dozens of models share
exactly — so nothing lands on a threshold by a rounding accident. The fourth tier exists because
the third otherwise spanned $2.10 to $30/M input; it isolates the seventeen models where a single
game can cost more than everything else on the site put together.

**A game costs the sum of its seats.** Two models means two prices added. A person plays for free
as themselves; only the machine seat is charged.

**The charge happens at creation, atomically**, exactly as the game-count reservation did — one
statement whose `WHERE` clause is the check, so concurrent requests cannot both spend the last
credit. A game that is resigned immediately still cost a credit; counting on completion would let a
user open any number of games at once and discover the price when the money was already spent.

**Prices are derived at catalogue sync, and an administrator can override any of them.** The
derived value and the override are separate columns: re-seeding rewrites the derived one and never
touches the override, so an exception survives a refresh.

## Alternatives considered

- **A dollar allowance.** A credit worth $X of provider spend, drawn down as a game plays. Exact,
  and no cross-subsidy — but it displays as "$0.37 remaining", and a game can die mid-move when the
  balance runs out, which is a worse experience than being refused at the door.
- **One flat credit per game.** What we had. Simple, and it prices a 300-fold cost range as though
  it were flat.
- **Deriving the price from a modelled game cost** (tokens × price, using an observed 700k-prompt
  profile). It was the first proposal here and it is strictly worse: it needs assumptions about
  game length and cache behaviour that vary by an order of magnitude between providers — two
  measured games cached at 96% and 0% — to arrive at an ordering that raw prices already give.
- **Keeping a daily reset alongside the balance.** Two systems to explain, and the reset undermines
  the point of a grant.

## Consequences

- **Credits are access control, not cost accounting.** Four tiers cannot price a 300-fold range:
  tier 4 spans $10 to $30/M, so a 6-credit game can cost three times another 6-credit game. The
  thing that actually bounds a bill is still `MAX_USD_PER_GAME` (layer 3), and the global daily
  kill switch (layer 1) remains the backstop that trusts nothing — including this arithmetic.
- **A new account cannot play.** That is the intent for the testing phase, and it is a dead end for
  anyone arriving later. A signup grant is the obvious future change; the balance model supports it
  by changing one default.
- **Pricing data becomes load-bearing twice over** — it already set the caps, and now it sets what
  users are charged. `make seed-models` reads the live catalogue for exactly this reason; a stale
  price is now both a wrong cap and a wrong price.
- **Granting is manual and out of band.** No request flow exists in the product. A user at zero is
  told to get in touch, and the administrator grants through the admin surface.
- **The daily ledger stays, demoted.** `usage_ledger` no longer gates anything, but it still
  records games started and dollars spent per day, which is what the admin spend view reads.

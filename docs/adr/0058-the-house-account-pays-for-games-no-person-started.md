# 0058. The house account pays for games no person started

**Status:** Accepted
**Date:** 2026-09-30
**Amends:** [0052](0052-credit-is-dollars-spent-at-actual-cost.md). "A tournament's or an operator's
game has no payer" becomes: they are paid for by the house account.

## Context

ADR-0052 charged a game to the person who started it. Tournament and operator games had no payer,
so their turns reached nobody's ledger. That was harmless while all spending was ours. It stopped
being harmless once credit was sold (ADR-0055, ADR-0056):
- Every dollar a user holds is backed by OpenRouter's prepaid balance.
- Tournaments spend from that same balance.
- Nothing tied the two together. Tournaments were capped only by their own budgets and the daily
  kill switch, and neither knows how much of OpenRouter's balance belongs to users.

So a busy tournament could spend OpenRouter below what users held. Their balances would still read
correctly, and their next turn would get a 402, which halts every model call. The $10 house reserve
in the sales headroom narrowed that gap but didn't close it: a tournament can spend $10 in an
afternoon.

## Decision

**Chessmark has an account, and it pays for every game no person started.**
- **The account is a `users` row** with `clerk_user_id = 'chessmark:house'` and the display name
  "Chessmark". A migration creates it with a zero balance. The id is not a Clerk id, and nobody
  signs in as it.
- **The payer of a game** is the person who started it, or else the house. One function decides
  (`db.house.payer_of`), and every place that charges, checks, pauses, resumes or settles asks it.
- **The house pays exactly as a person does.** Each turn is charged at its cost. A turn is refused
  while the balance is at or below zero, and the game pauses, saying it is "waiting for Chessmark
  to add credit". It resumes on the first sweep after the house is funded, and billing
  reconciliation settles house games like any other.
- **A tournament with paid entrants holds while the house is empty**, the same way it holds for a
  halt. Otherwise a pool would keep starting games that pause at their first paid turn. A
  free-only tournament costs the house nothing and never holds for it.
- **The house's positive balance counts as credit held** in the sales headroom. So what the house
  is given is never sold again, and a tournament can't spend what users hold.
- **An operator funds it** with `./chessmark credits house <usd>`, and `./chessmark status` shows
  its balance.

**`games.created_by_user_id` is unchanged.** It still means who started a game, and the game page
still names the event rather than a person. The house is the payer; nobody claims to have started
the game.

**It applies to existing games from their next turn**, because the payer is resolved when a turn
is played, not stored. Games that finished before the house existed were never charged, and billing
reconciliation leaves them alone. A game that was running across the change has some turns that
were never charged. When it is reconciled, the house is settled to the whole bill, including those
turns. That can overstate what the house spent by one game's early turns, never understate it.

## Alternatives considered

- **A second OpenRouter account for tournaments.** It separates the money completely, but there
  would be two balances to top up and watch, and a second set of keys on the server.
- **A check before each tournament turn**, pausing when OpenRouter's balance minus what users hold
  drops below a line. That's less code, but tournament spending stays off the ledger, and the only
  limit remains the per-event budget.
- **Storing the house as each tournament game's `created_by_user_id`.** It needs no lookup, but
  "Started by Chessmark" would be a false statement on every tournament game, and old games would
  need a backfill.

## Consequences

- **After deploying, tournaments with paid models hold until the house is funded.** That's
  deliberate: the migration grants no money. Fund it with `./chessmark credits house 20` or
  similar.
- **The house needs topping up** as tournaments spend. `./chessmark status` warns when it is empty,
  and tournaments hold rather than fail.
- **`CREDIT_RESERVE_USD` now only needs to cover turns in flight**, since the house balance covers
  tournaments. Its default is unchanged at $10; lowering it is the owner's call.
- **With no house row, the old rule applies:** such games are charged to nobody. That's how the
  test suite runs, since it truncates every table between cases. Every real database has the row,
  and `status` reports it if it's missing.
- Nobody signs in as the house, so the identity backfill skips it.

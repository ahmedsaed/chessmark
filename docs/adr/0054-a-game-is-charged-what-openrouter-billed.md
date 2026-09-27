# 0054. A game is reconciled against what OpenRouter billed, and charged it

**Status:** Accepted
**Date:** 2026-09-27
**Amends:** [0052](0052-credit-is-dollars-spent-at-actual-cost.md). A game's payer is charged what
OpenRouter billed for it, not only what our record holds.

## Context

ADR-0052 charges each turn at the cost its calls recorded, and that record matched OpenRouter to
the micro-dollar for every call it held: sixty sampled, sixty exact, cache discounts included. But
some calls were answered, billed, and never recorded, so the record fell short of the bill. Every
request a game makes carries `session_id = game-<id>`, and OpenRouter's analytics can group by it,
so a game's record can be checked against the bill:

* Across production's 155 game sessions: **$0.4622 billed, $0.4144 recorded**, and 1,883 billed
  requests (6.3%) that no record of ours held.
* The causes, traced generation by generation: turns rolled back after answers were billed, fixed
  by [ADR-0053](0053-every-failure-keeps-its-rounds.md); a crash loop on resume (#118, #119);
  refused requests, which cost nothing.

Tracking every way a call can be lost, one failure at a time, is a fight that never ends: there is
always another path. The owner chose the other direction: check each game against what OpenRouter
billed for it, and charge that.

Two facts shaped how:

* **The analytics API truncates each generation's cost to six decimal places.** `0.000062496`
  reads `0.000062`. That made every decision game look 1% overcharged: its record, $0.008313522,
  against an analytics total of $0.008234. Summed from OpenRouter's own per-generation figures, the
  same game comes to $0.008313522 exactly. The record was right; the aggregate is lossy. The
  analytics API finds generations. It cannot price them.
* **It takes `Z` timestamps only.** `+00:00` is refused as "Invalid ISO datetime".

## Decision

**A game is reconciled when it comes to rest**:
* when it ends;
* when its owner pauses it;
* when it pauses because its owner is out of credit.

Those last two may never lift. A provider's pause is not a rest: it lifts by itself within minutes,
and there were 1,468 of them in a month.

**Each game is checked three times, about 5 minutes, 1 hour and 1 day after it comes to rest**,
because the analytics lag. Each check recomputes the total from scratch and settles only what
changed, so nothing is counted twice and nothing late is missed. A game that resumes and plays on
has a new event cursor, and its schedule starts again. Whether a check is owed is decided in SQL:
the first real run showed that taking a hundred games at rest and then asking which were due hid
every game behind them.

**The billed total is exact.** The analytics API (management key) lists the session's generations.
Those our record holds keep their recorded cost, which is OpenRouter's per-generation figure. Those
it does not hold are priced one by one from `GET /generation`, stored in `unrecorded_generations`,
and never looked up twice. Whatever cannot be priced in a sweep is counted at its truncated floor,
so the total is not short, and it is priced exactly on the next check.

**The payer is charged what OpenRouter billed.** This is the owner's decision: a round our failure
lost was billed all the same, and after ADR-0053 such losses should be rare. The difference goes
on the ledger as one `settlement` row per change, and it may be a refund. It is never refused or
clamped, so a balance may end a game's settlement below zero, as with a turn's overrun (ADR-0052).
Only a game charged in dollars is settled. A game from the credits era is recorded, not charged.

**The page says so.** While a game plays, its cost is what it recorded. Once reconciled, it shows
**Billed** and the billed figure. An ⓘ, which is a button so it works on a phone and a keyboard,
explains any difference: how many requests, what they cost, and that they are included.

**`status` has the safety net under it all**: this month's usage on the key itself, against our
recorded calls plus every generation reconciliation found. A gap that grows is spend that neither
can see.

## Alternatives considered

* **Record every billed call as it arrives, in its own transaction.** It catches only failures our
  own code sees, and every new failure mode means changing it again. The session check catches all
  of them, including ones we have not found yet.
* **Settle to the analytics total.** It is 1% short on small calls, and would have refunded money
  that was really charged.
* **Reconcile on every pause.** Provider pauses resume by themselves, and there are about fifty a
  day.
* **Absorb the difference.** It was offered. The owner chose to charge it, because it should be
  rare.

## Consequences

* **A game's charge is final about a day after it comes to rest**, not when it ends. The
  settlement rows say so.
* **One analytics query per game per check**, plus a lookup per generation we lost. Sweeps are
  capped at 20 games and 200 lookups, and a backlog is worked through over minutes. The first run
  over production's 30 days reconciled 168 games: $0.4332 billed against $0.3926 recorded, and
  $0.4332 against the key's own September usage of $0.4334.
* **Games older than 30 days are never reconciled**, because OpenRouter keeps per-generation data
  for 31 days.
* **The management key lives only on the server.** It can create and delete keys, and we only
  read with it.

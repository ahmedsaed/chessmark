# 0052. Credit is dollars, spent at what each turn actually cost

**Status:** Accepted
**Date:** 2026-09-26
**Supersedes:** [0016](0016-credits-as-a-granted-balance.md), except its decision that a balance is
granted rather than regenerating, which stands.

## Context

ADR-0016 made a credit **a unit of play**. A game cost one to six credits, charged when it was
created, by the price band of its models. It said plainly that this was access control, not cost
accounting. That was right while credit was only granted, and it cannot survive being sold
([PAYMENTS.md](../PAYMENTS.md)):

* **A band cannot price a game.** Two models in one band differ by up to threefold on list price
  alone. A game's real cost also depends on how much a model writes, where a reasoning model can
  write 10–50× a plain one, on caching, which measured 0% and 96% on two games, and on length,
  which runs from 2 to 300 plies. A cash price per credit would overcharge most games in a band
  and lose money on some.
* **Length did not count.** A resignation at move one cost the same as a 150-ply game.
* **A game we failed kept its charge.** `refund()` existed, and nothing called it.

The services that sell AI usage have converged on one answer. Credit is dollars, each request is
charged the actual cost of its tokens, and a flat price per action is abandoned once the actions
vary this much. OpenRouter, our own provider, works this way, and so moved Cursor and Replit in
2025.

The owner chose that, and chose against two refinements of it:

* **No holds and no estimates.** A game does not reserve a ceiling when it starts. Nothing guesses
  what a game will cost before it is played.
* **No negative balance as a design.** Letting a game run into debt would need a limit of one game
  at a time to contain it, and a new account could walk away from the debt. Instead, a game
  **pauses** when its owner is out, and resumes when credit is added.

## Decision

**A balance is US dollars** (`users.balance_usd`). New accounts hold zero, and an administrator
grants credit in dollars. That is still the only way a balance rises until payments exist.

**A game is charged turn by turn, at the turn's actual cost**, computed from the token counts the
provider returned (invariant 4). The payer is the person who started the game
(`games.created_by_user_id`). A tournament's or an operator's game has no payer. Each charge is:

* a `turn` row on `credit_ledger`, naming the game and the turn it paid for;
* debited **in the same transaction** that adds the same number to the player's and the game's
  `total_cost_usd`, so what a game says it cost and what its owner paid cannot disagree;
* rolled back with its turn, so a turn that is retried is never charged twice, and a turn that
  never committed is never charged at all.

**The debit is never refused.** The money is spent by the time the cost is known. What stops play
is the worker's check **before** a turn: the payer's balance must be above zero. A turn that starts
with any credit runs, its cost can take the balance below zero, and the next turn stops. **A
balance can therefore overrun by the one turn in flight per running game, and never more.** This is
bounded by construction, not by a rule we enforce.

**Out of credit, a game pauses.** It uses the existing pause machinery:

* The pause reason begins `out of credit:`, and its `game_paused` event carries `credit_of`.
* The reconciler holds it while the owner's balance is at or below zero, and resumes it on the
  first sweep after the balance rises. Adding credit is enough; nothing has to find the game.
* The game page says it is waiting for its owner to add credit.
* The pause is kept off the abandonment clock, like a halt, because an empty balance is not
  something the model did.

**Starting a game checks and charges nothing more:**

* A game against a paid model needs a balance above zero.
* A person playing a `:free` model needs nothing, because its turns cost nothing.
* A game between two models always needs a balance, since running machines is not playing them.
* A `:free` seat is never paused for credit.

**Nothing is refunded.** The owner's decision: a person pays for the turns that were played, and
those turns are the experience, whether or not the game reached a result. A turn rolled back by a
provider failure was never charged, which is the only case where "we failed" meant nothing was
delivered.

**The price band survives as a band.** `model_registry.price_tier` is 1 to 4, the same four
clusters ADR-0016 found. It is shown as `$`–`$$$$` in the picker, or `free`, and it selects
tournament fields (`--max-tier 1`). It is no longer a charge. The administrator's override is
removed: it existed to change what a user was charged.

**Every existing balance was reset to zero**, the owner's choice, since only testers held any. The
ledger keeps its credit-era rows with `unit = 'credit'`, each balance closed by one `retired` row,
so the old history still sums correctly. The dollar balance sums only `usd` rows.

## Alternatives considered

* **A hold, then settle.** Reserve the per-game ceiling at the start, charge actual cost, release
  the rest. A game could never run out mid-play, but a $4.50 balance would allow four games at once
  when most cost ten cents. Rejected by the owner as too much logic for the gain.
* **A negative balance, one game at a time.** Let the game finish, and recover the debt later.
  Rejected: the concurrency rule exists only to contain the debt, and a new account escapes it.
* **Measured per-model prices, charged at the start.** Predictable, but averages hide exactly the
  variance that makes a flat price lose money, and length still does not count.
* **Refunding the spend of a game we abandon.** The owner declined: the turns were played and
  paid for with the provider.

## Consequences

* **A game's price is known only when it ends.** The page says so before a game starts, because
  there is no number to show.
* **The header balance follows play without polling.** It re-reads `/me` on navigation, when the
  tab comes back into view, and when the page of a game the reader pays for shows a model move.
  That is the only event that changes a balance while someone watches. Who started a game is
  private, so the seat endpoint tells the owner alone (`pays`). A signed-in viewer of a live game
  makes one extra request per page load to learn it, and anonymous readers make none.
* **The per-turn ledger is one row per model turn.** It grows with play at the rate `turns` does,
  and it is what lets a balance be explained move by move.
* **`MAX_USD_PER_GAME` still ends a game as `budget_exceeded`.** It is the harness's own bound
  (ADR-0011, layer 3), not the user's balance. A game someone pays for can meet it. Its value
  should be set from real per-game costs, which have not been measured against production yet.
* **This is the credit system a payment processor needs.** A purchase is a grant with its own
  reason, and a dollar bought is a dollar spent at cost ([PAYMENTS.md](../PAYMENTS.md)).

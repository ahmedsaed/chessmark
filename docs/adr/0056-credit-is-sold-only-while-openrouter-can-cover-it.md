# 0056. Credit is sold only while OpenRouter can cover it, in any amount the buyer chooses

**Status:** Accepted
**Date:** 2026-09-28
**Amends:** [0055](0055-credit-is-sold-as-fixed-packs-through-paddle.md). The fixed $5 / $10 / $25
packs become any whole-dollar amount from $5 to $100. The breakdown gains an AI provider fee line.
A purchase is credited from a reservation the server made, not from a catalogue price. Credit may
now expire.

## Context

ADR-0055 sold three fixed packs and granted each one what the server's own table said. Three things
it did not account for:

* **Our OpenRouter account is prepaid.** Every dollar of Chessmark credit is a promise of OpenRouter
  usage, and our own tournaments draw on the same balance. Selling more credit than that balance
  covers would sell games that fail at the provider.
* **OpenRouter charges 5.5% when we buy its credit by card**, with a $0.80 minimum per top-up
  ([its FAQ](https://openrouter.ai/docs/faq)). A dollar of Chessmark credit therefore costs us $1.055
  of OpenRouter's. ADR-0055's margin kept only 5% for running the site, so every sale lost money.
* **OpenRouter reserves the right to expire unused credit after a year.** Our terms promised that
  Chessmark credit never expires.

The owner also wanted buyers to choose any amount rather than one of three.

## Decision

**A fourth line in every purchase: the AI provider fee.** A purchase is the sum the page lays out:

| | $5 | $10 | $25 |
| --- | --- | --- | --- |
| Payment processor (5% + $0.50) | −$0.75 | −$1.00 | −$1.75 |
| Running Chessmark (5%) | −$0.25 | −$0.50 | −$1.25 |
| AI provider fee (5.5%) | −$0.21 | −$0.45 | −$1.15 |
| **Credit** | **$3.79** | **$8.05** | **$20.85** |

The first two round up to the cent. The credit is what pays for itself plus OpenRouter's 5.5%,
rounded down, and the AI provider line is the remainder. So the lines always add up to the price, and
rounding never grants more than the rule allows. Tax is still added on top.

**Any whole number of dollars from $5 to $100.** $5, $10 and $25 remain as quick picks.
- The floor keeps Paddle's fixed $0.50 from eating most of a purchase: $5 buys 76% of its price in
  credit, $100 buys 85%.
- The ceiling caps what one refund or chargeback can cost us, and how much of the headroom one buyer
  can take.

**The server creates each purchase's transaction** at the chosen price, using a non-catalogue price
of the "Chessmark credit" product. The browser opens Paddle's checkout on that transaction. That
needs a server-side Paddle API key (`PADDLE_API_KEY`). The owner chose this over the one alternative
that needs no key: a $1 catalogue price bought in quantity, whose checkout line reads "37 × $1.00".

**Credit is sold only while OpenRouter can cover it:**

```
headroom = OpenRouter remaining                  (/api/v1/credits, management key, cached a minute)
         − credit users already hold             (positive balances)
         − credit reserved by open checkouts
         − the house reserve                     ($10: our tournaments, and turns in flight)
```

**A checkout reserves its credit before Paddle is asked for anything.** In one transaction, under a
transaction-scoped advisory lock, the API computes the headroom and inserts a reservation. A second
buyer's check waits on the lock, and when it runs, the first buyer's reservation already exists and
is counted. So two buyers cannot both take the last of the headroom. The lock is never held across
the call to Paddle: the reservation commits first, and if Paddle then refuses, the reservation is
released.

**The reservation is what a purchase grants.** Its id goes in the transaction's `custom_data`,
written by the server. When Paddle reports the payment, the webhook:
- checks that the pre-tax amount paid equals the amount reserved;
- credits the reserved amount;
- marks the reservation consumed.

All three happen in one database transaction, so the credit moves from reserved to held without
being counted twice or not at all. A payment that matches no reservation, or not its own, is
recorded as `unmatched` and not credited.

**When the balance can't be read or doesn't cover the amount, the buyer is told.** An unknown
OpenRouter balance sells nothing (503); it is never treated as a large one. An amount the headroom
cannot cover is refused (409) with the largest amount that can be bought now, or "sold out for now".
A reservation that expires frees its share after 30 minutes. A payment completed after its
reservation expired is still credited: the money has moved. `./chessmark status` reports it, since it
may have sold past the headroom.

**Credit unused for 12 months may expire.** The terms say so, and so does the credit page. A
purchase or a paid move restarts the 12 months. This is a right we reserve, matching OpenRouter's,
and nothing enforces it automatically.

## Consequences

* New settings:
  - `PADDLE_API_KEY`, which can create transactions;
  - `PADDLE_PRODUCT_ID`;
  - `CREDIT_RESERVE_USD`.

  `PADDLE_PRICE_IDS` is gone, and the fixed pack prices in both Paddle catalogues are archived.
  Selling also requires `OPENROUTER_MANAGEMENT_KEY`.
* New `credit_reservations` table, and a `reservation_id` column on `purchases`.
* **"Sold out" is now a state the site can be in.** It means top OpenRouter up. `status` shows the
  headroom and the largest amount that can be bought.
* A buyer only learns an amount is unavailable when they press Buy. The page does not read the
  headroom on every view, because that would call OpenRouter on every render.
* The margin still shrinks where there is tax (ADR-0055: Paddle's fee is on the total including tax),
  and still loses Paddle's fee on a refund. The AI provider line covers OpenRouter's fee. It does not
  cover OpenRouter's $0.80 minimum on a small top-up; buy OpenRouter credit in amounts well above $15
  and that minimum doesn't come into play.

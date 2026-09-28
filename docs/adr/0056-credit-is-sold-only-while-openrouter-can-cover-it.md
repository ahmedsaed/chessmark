# 0056. Credit is sold only while OpenRouter can cover it, in any amount the buyer chooses

**Status:** Accepted
**Date:** 2026-09-28
**Amends:** [0055](0055-credit-is-sold-as-fixed-packs-through-paddle.md). The fixed $5 / $10 / $25
packs become any whole-dollar amount from $5 to $100. The breakdown gains an AI provider fee line.
The amount chosen now includes any tax, where 0055 added tax on top. A purchase is credited from a
reservation the server made and the tax Paddle charged, not from a catalogue price. Credit may now
expire.

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
rounding never grants more than the rule allows. The table is a buyer who pays no tax.

**The amount the buyer chooses is what they pay, tax included.** ADR-0055 added tax on top, so a
buyer who chose $37 paid $41.54 and read two numbers for one purchase. Now tax comes out of the
amount first, as one more line of the same sum, and the three shares are taken from what is left:

| $37, 14% VAT | |
| --- | --- |
| Tax | −$4.54 |
| Payment processor (5% + $0.50 of $37) | −$2.35 |
| Running Chessmark (5% of $32.46) | −$1.63 |
| AI provider fee (5.5%) | −$1.49 |
| **Credit** | **$26.99** |

The processor's share is on the whole $37, because Paddle's fee is on the total including tax. The
other two are on the amount after tax, which is what actually reaches us. A buyer in a country with
tax therefore gets less credit for the same amount than one without, where under 0055 they paid
more for the same credit. Either way the tax is the state's, not ours; this way the number they
chose is the number on their statement.

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
headroom = OpenRouter remaining                  (/api/v1/credits, management key)
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

**OpenRouter's balance is read in two ways, for two readers.** The worker reads it once a minute,
beside its billing sweep, and stores it. The credit page reads that stored value, so it can say
"sold out for now" or "up to $X right now" before anyone presses Buy, and **a page view never calls
OpenRouter**. Buy reads OpenRouter fresh, because it is about to promise credit, and stores what it
read. So OpenRouter is asked once a minute, plus once per Buy, however many people view the page. A
stored value more than ten minutes old stands for nothing: the page says buying is paused, rather
than trusting a figure from before the worker stopped.

**Tax is estimated on the page, and confirmed at checkout.** Paddle can preview the tax for a
visitor's country, detected from their IP address, but it previews only catalogue prices. Purchases
use prices the server creates. So there is one catalogue price of $1, never sold, and the page
previews it in the chosen quantity: $37 is a preview of 37. The breakdown then reads as one sum:
the amount, the tax (marked estimated) taken out first, the three shares, and the credit (marked
"about"). The checkout dialog shows the same sum with the tax Paddle's checkout reports, and the
credit it gives. It is an estimate because the final tax depends on the country the buyer confirms,
a VAT number, or a US ZIP code. Without a preview price configured, the page shows the sum with no
tax line, and says that any tax comes out of the amount at checkout.

**One open checkout per person, and five a minute at most.** Without the first rule, pressing Buy
repeatedly would hold the headroom for thirty minutes per press, and one person could sell the site
out for everyone without paying. A new checkout therefore releases the buyer's earlier one. If the
earlier one is paid after all, it is still credited: the money has moved, and the fresh read before
every reservation limits what that can cost. The rate limit stops a script from using the page to
hammer OpenRouter.

**The reservation is what a purchase grants.** Its id goes in the transaction's `custom_data`,
written by the server. When Paddle reports the payment, the webhook:
- checks that the total paid, tax included, equals the amount reserved;
- credits the quote for that amount less **the tax Paddle reports it charged**, not the estimate;
- marks the reservation consumed.

A reservation holds the credit the amount would buy with no tax, the most it can grant, so the
headroom is never short by the difference.

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
* The page's "sold out" and "up to $X" come from a balance up to a minute old, so they are a hint.
  Buy is what decides, and a buyer can be refused there after the page said yes, when the balance
  fell in between.
* The $1 preview price is public, like every catalogue price, so a determined visitor could open a
  Paddle checkout on it directly. Such a purchase has no reservation, so it is recorded as
  `unmatched` and not credited, and an operator settles it. Its quantity is limited to 5–100, the
  same range as real purchases.
* `/credit` loads Paddle.js for every visitor while selling is on, not only for buyers, because the
  estimate needs it. Each change of amount makes one preview request, sent once typing pauses.
* Selling needs the worker running, since it keeps the stored balance current. With no worker, the
  page reports buying as paused within ten minutes.
* Where there is tax, the buyer's credit shrinks and our margin doesn't: the processor's share is
  taken on the total including tax, and the running share on what is left after it. A refund still
  loses Paddle's fee.
* The credit a buyer sees before paying is exact only in the checkout dialog. On the page it is an
  estimate, and the webhook grants what the actual tax leaves. The AI provider line covers OpenRouter's fee. It does not
  cover OpenRouter's $0.80 minimum on a small top-up; buy OpenRouter credit in amounts well above $15
  and that minimum doesn't come into play.

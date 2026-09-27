# 0055. Credit is sold as fixed packs through Paddle, and credited by its webhook

**Status:** Accepted
**Date:** 2026-09-27
**Builds on:** [0052](0052-credit-is-dollars-spent-at-actual-cost.md), which made credit a dollar
balance spent at cost. This decides how that balance is bought.

## Context

Credit could only be granted by an administrator. [PAYMENTS.md](../PAYMENTS.md) found the route to
selling it: Chessmark has no company behind it and its owner is in Egypt, so the seller of record
has to be a **merchant of record** that takes individuals, pays out to Egypt, and handles consumer
tax worldwide. That is Paddle. Paddle's approval is still pending — it has been asked whether
prepaid usage credit counts as "stored value" under its acceptable use policy — so this is built
against Paddle's sandbox and ships switched off.

## Decision

**Three one-time packs, $5, $10 and $25, each granting a fixed amount shown before payment.**

* **No fee is absorbed** (the owner's rule). Paddle keeps 5% + $0.50 of a sale and 5% more is kept
  for infrastructure, so a pack grants `price × 0.90 − $0.50`: **$4.00, $8.50, $22.00**. The amount
  is exact and stated on the page and in Paddle's own checkout ("$4.00 Chessmark credit"), never
  estimated from what one sale's fee turned out to be — the same principle as ADR-0052, applied to
  buying.
* **Tax is added on top.** Paddle's prices were first tax-inclusive, which meant every sale in a VAT
  country lost money: a German buyer's $5.00 held $0.80 of VAT, leaving $3.45 after Paddle's fee
  against $4.00 granted. With tax on top, what reaches us is always the pack price less Paddle's fee,
  in every country. The page says "+ tax where it applies"; Paddle's checkout shows the amount.
* **One pack per checkout** (`quantity` fixed at 1), so a purchase is always one known amount.

**What a pack grants is decided on the server, by the price paid.** The browser opens Paddle's
overlay naming a price, and carries the buyer's Clerk id in `custom_data`. Paddle's signed
`transaction.completed` webhook names the price that was actually paid; the API maps it to credit
from its own table (`core/credit_packs.py`). Nothing the browser sends can change the amount. The one
buyer-supplied value that is trusted, the account id, can at worst credit a pack to someone else's
account — a gift, not a theft.

**The webhook is the only thing that moves a balance, and it moves it once.**

* Verified by hand: HMAC-SHA256 over `{ts}:{raw body}` with the destination's secret, constant-time,
  every offered `h1` tried, a five-minute window. No SDK — the same reasoning as the Clerk webhook.
* **A purchase credits once per Paddle transaction**, enforced by a unique constraint on the
  transaction id rather than by read-then-write, so concurrent redeliveries cannot both credit.
* **A purchase that cannot be matched** — an unknown price, no matching account — is recorded as
  `unmatched`, acknowledged, and not credited. Refusing it would make Paddle retry for three days
  over money that has already moved; an operator settles it by hand.
* **Refunds and chargebacks take back what the purchase granted, in proportion to the money
  returned**, once each (unique on the adjustment id), only once approved, never more than the
  purchase granted in total, and **never floored at zero**: the refund policy says a balance can go
  below zero this way, and paid games then pause until it is above zero again. A chargeback that is
  reversed restores it.

**After payment the page waits for the webhook.** It asks about that one transaction every two
seconds for up to a minute and says "added" only when the API has recorded it — the money has moved,
the credit arrives by a different road, and saying otherwise would be contradicted by the header.

**Off unless configured, on both sides.** The API sells only with both `PADDLE_WEBHOOK_SECRET` and
`PADDLE_PRICE_IDS`, and production refuses to boot with one and not the other (prices without the
secret would take money it could never credit). The web build sells only with a Paddle client
token, and refuses to build with a token and no environment, or a token from the other environment:
a sandbox token pointed at production, or the reverse, fails in front of a buyer.

## Consequences

* A **purchases** table (one row per Paddle transaction, keeping what the buyer paid, the tax,
  Paddle's fee and our earnings verbatim) and **purchase_adjustments**. Purchases outlive the
  account (`user_id SET NULL`), because a refund can arrive after the buyer deleted it.
* **Paddle charges its fee on the total including tax**, measured on the first sandbox purchase:
  a $5 pack bought from Egypt (14% VAT) came to $5.70, Paddle kept $0.79 rather than $0.75, and
  $4.21 reached us against $4.00 granted. So the 5% for running the site is 5% where there is no
  tax and a little less where there is — about 3.6% at the highest VAT rate (27%) — and never a
  loss. Accepted rather than priced per country, which would make the credit a pack grants depend
  on where its buyer lives. Each purchase records Paddle's actual `fee` and `earnings`.
* **A refund costs us Paddle's fee.** Paddle keeps its fee when it refunds a payment
  (`retained_fee`), measured on a full sandbox refund: the buyer got $5.70 back, the credit went
  back to where it was, and we were $0.79 down. One refund undoes the margin of three to eight
  sales, depending on pack and tax. Accepted by the owner: the refund policy keeps refunds rare —
  only an untouched purchase within 14 days, which EU and UK law requires anyway, while a broken
  game is made good in credit, which costs no fee. Refunding less than the full price would need a
  second rule for EU and UK buyers, whose statutory refund is full. A chargeback can carry a fee of
  its own on top, and is the rate Paddle watches.
* **Selling needs the owner's steps in Paddle's dashboard**, which no API sets: the default payment
  link, the notification destination, and website approval on live.
* The worst purchase failure is a paid, uncredited purchase. It is visible (the page tells the buyer
  to write to support, and the row is `unmatched`), and its fix is a manual grant.
* Sandbox and live are separate Paddle accounts. Every id — prices, token, secret — changes when the
  live catalogue is created, and only the configuration changes with it.

# Payments — selling credits, if we ever do

**Status: research, not a decision.** Nothing here is built. Credits are granted by an
administrator and have no cash price ([ADR-0016](adr/0016-credits-as-a-granted-balance.md)). This
file holds what was found about *how* they could be sold, so that when the owner decides whether
to sell them, the processor, logistical and legal constraints are already written down rather than
re-researched. Once that decision is made, it gets an ADR, and this file becomes the guide to it.

Researched 2026-09-26. **Every provider fact below is a claim from the provider's own pages as of
that date.** Seller-country lists and onboarding rules change without notice, so re-check them at
the moment of applying ([sources](#sources)).

## The constraints

* **Chessmark is a personal project.** There is no company behind it, and forming one only to
  take payments is a cost to avoid unless nothing else works.
* **The owner is in Egypt, and buyers would be anywhere.** Any route has to accept worldwide cards
  and pay out to Egypt.
* **International sales carry consumer taxes** (EU VAT, UK VAT, US state sales tax, and others),
  each with its own registration and filing. For one person that is the part that does not scale,
  and it is harder than anything in the code.

## The finding: a merchant of record, not a payment processor

A **payment processor** (Stripe, for example) moves money on the seller's behalf, and the seller
remains the legal seller. The seller owns every tax obligation in every buyer's country, and must
be a business the processor onboards in its own country.

A **merchant of record** (MoR) buys the product from us and resells it to the customer. The MoR is
the legal seller: its name is on the customer's receipt, and it calculates, collects and remits
consumer tax in every country. We are its supplier, paid out as an individual. This is the usual
route for solo software sellers, and it answers all three constraints at once.

| Route | Company needed? | Consumer tax | Pays out to Egypt | Cost |
| --- | --- | --- | --- | --- |
| **Paddle** (MoR) | **No.** Individuals and sole traders sell without business verification | Handled, worldwide | **Yes.** Egypt is on the supported list | ~5% + $0.50 per sale |
| **Lemon Squeezy** (MoR) | No | Handled | Bank payouts in 79 countries and PayPal in 200+; **Egypt not confirmed** for bank payouts | ~5% + $0.50 per sale |
| **Stripe** (processor) | **Yes.** Stripe does not onboard merchants based in Egypt, so it needs a US LLC (Stripe Atlas, $500) | **Ours, everywhere** | To a US bank account | ~2.9% + $0.30, plus the LLC's running costs |

**Paddle is the recommendation.** It explicitly takes individuals, lists Egypt, and removes the tax
problem.

**Stripe through a US LLC was rejected** for this project. It saves about two points per sale and
costs a company, a US bank account, a registered agent, and annual filings. Missing IRS Form 5472
for a foreign-owned LLC carries a **$25,000 penalty per return**. On top of that, all consumer tax
in every buyer's country would become ours to register for and file. That is the right trade for a
business at volume and the wrong one for a personal project.

## What onboarding asks for

An MoR is also taking on our legal and reputational risk, so it reviews the seller before
approving them:

* **A live site that shows what is being sold.** Visible pricing, **terms of service**, a **refund
  policy** and a **privacy policy** — none of which Chessmark has today.
* **Identity verification**, even for an individual: ID, and possibly proof of address.
* **An acceptable product.** Prepaid credits spent on AI usage are normally treated as ordinary
  SaaS, but some MoRs are wary of anything resembling a virtual currency. **Ask Paddle's support
  before building anything**, since a refusal here voids the rest of the plan.

## What stays with us, even with an MoR

* **Income tax on the payouts.** The MoR handles the *customer's* taxes, not ours. Payouts are the
  owner's personal income in Egypt, to be declared there. Whether that counts as freelance income
  or needs a tax registration is a question for an Egyptian accountant, not for this document.
* **Disputes and chargebacks.** The MoR routes disputes to us and closes accounts whose chargeback
  rate is high, so the refund policy has to be one we honour without argument. That makes the gap
  below a prerequisite.
* **Personal liability.** Without a company, any claim is against the owner personally. The
  exposure is small for a chess benchmark, but the terms should cap liability and state that
  credits have no cash value and cannot be withdrawn.

## Prerequisites in our own product

Before a credit has a cash price, the following must be settled:

1. **A game we fail must give its credits back.** Credits are charged when a game is created
   (ADR-0016), and `db/credits.refund()` exists with its own ledger reason, but **nothing calls
   it.** A game abandoned by a harness failure keeps its charge. With granted credits that is
   invisible; with paid ones, it charges a customer for our fault and invites the chargebacks
   above. This is invariant 11's reasoning applied to money.
2. **A credit's price has to cover what a game can cost.** Today it cannot: credits are access
   control, not cost accounting (ADR-0016). A tier spans up to a threefold range of provider
   prices, so a cash price per credit either overcharges most games in a tier or loses money on
   some. `MAX_USD_PER_GAME` ($1.00) bounds the loss but does not remove it. This is the next
   discussion: how credits should be priced.
3. **Terms of service, a refund policy and a privacy policy**, published before onboarding.

## Sources

* [Paddle — which countries are supported](https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle)
* [Paddle — business verification](https://www.paddle.com/help/start/account-verification/what-is-business-verification)
* [Paddle — supported countries (developer docs)](https://developer.paddle.com/concepts/sell/supported-countries-locales/)
* [Lemon Squeezy — supported countries](https://docs.lemonsqueezy.com/help/getting-started/supported-countries)
* [Lemon Squeezy — getting paid](https://docs.lemonsqueezy.com/help/getting-started/getting-paid)
* [Stripe — opening a US LLC as a non-resident](https://stripe.com/resources/more/how-to-open-an-llc-in-the-usa-for-nonresidents)
* [Stripe Atlas vs Doola for non-US founders](https://www.theamericanllc.com/blog/stripe-atlas-vs-doola-which-is-better-for-non-us-founders)

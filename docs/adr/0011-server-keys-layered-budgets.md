# 0011. Server-held API keys with four layers of budget control

**Status:** Accepted
**Date:** 2026-08-15

## Context

Anyone can start a game, and every game spends real money on someone's OpenRouter account — ours.
The failure mode is not theoretical: a loop bug, a scripted abuser, or one very long game against an
expensive model can quietly burn a budget overnight.

Requiring users to bring their own key would eliminate the risk entirely, at the cost of the
frictionless "click and watch two models fight" experience that is the whole product.

## Decision

Server-held keys only, defended by four **independent** layers:

| Layer | Mechanism | Effect when tripped |
| --- | --- | --- |
| Global | Redis counter of today's total spend, checked before every LLM call | all new calls refused |
| Per user | `usage_ledger` daily games and USD | user cannot start a game |
| Per game | Running total vs. `max_usd_per_game` | game ends `budget_exceeded` |
| Per turn | Token and wall-clock ceiling | turn forfeits |

Cost is computed from **actual returned token counts** against `model_registry` pricing, written to
the `llm_call` row and rolled up in the same transaction. Never estimated.

Keys never leave the worker tier. Phase 9 asserts this in CI by grepping the built client bundle.

## Alternatives considered

- **Bring-your-own key.** Zero cost and zero risk to us, but it puts a signup-and-paste wall in front
  of the thing people come for. Deferred, not rejected (AUTH-09) — it becomes attractive as a way to
  unlock expensive models once cost is the binding constraint.
- **A single global cap only.** Simple, but one abusive user can exhaust the day's budget for
  everyone. Layers exist so that no single bypass is fatal.

## Consequences

- The product stays frictionless: click, watch, no setup.
- Four layers is deliberate redundancy. Any one will eventually have a bug or be bypassed; they are
  independent so that a failure is contained rather than total.
- Cost accounting must be exact, which makes `model_registry` pricing data load-bearing. Stale
  pricing means wrong caps — it needs a refresh path and monitoring.
- Expensive frontier models may be restricted or run only in scheduled tournaments with their own
  budget, rather than being freely startable by any user.
- **Phase 9 is a hard gate.** Nothing is exposed publicly before these controls exist and are tested,
  including a test that fails if a provider is called while the global budget is tripped.

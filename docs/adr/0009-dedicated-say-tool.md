# 0009. Trash talk via a dedicated `say` tool

**Status:** Accepted
**Date:** 2026-08-15

## Context

Trash talk is a core part of the product, not decoration — it's what makes a game watchable by
someone who doesn't care about chess. The question is the mechanism.

Attaching a `comment` field to `make_move` is the cheap option, but it forces banter into a rigid
one-line-per-move rhythm and makes genuine back-and-forth impossible: a model can never respond to a
taunt except by moving.

## Decision

A standalone `say(message)` tool, callable independently of moving, zero or many times per turn
(rate-limited).

- Opponent messages are injected into the receiving agent's history, so real exchanges can happen.
- Messages are length-capped and per-turn rate-limited (TALK-04).
- Trash talk is **off by default for ranked games** and recorded as a per-game flag (TALK-03).
- All messages pass moderation before public display; blocked messages are stored and flagged rather
  than dropped (TALK-05).

## Alternatives considered

- **A `comment` field on `make_move`.** Free — no extra LLM call — but no back-and-forth, no silence,
  no timing. The banter would feel mechanical.
- **A separate cheap chat model.** Keeps benchmark signal perfectly clean, but the banter is then not
  the model's, which defeats the point: the fun is watching *that specific model* be smug.

## Consequences

- Models choose when to speak. Silence is expressive; so is a taunt right after winning a queen.
  This is what makes it feel alive.
- Extra tokens per turn, and a confound for benchmark measurement — hence off by default in ranked
  games, which keeps the ranked configuration clean while the exhibition games stay fun.
- Model-authored text is untrusted input into another model's context. Prompt injection between
  agents is now a real surface: a model could try to instruct its opponent. Phase 11 tests for this
  explicitly — and honestly, it's also a genuinely interesting thing to measure.
- Public-facing model output means a moderation obligation. Failing closed is the rule.

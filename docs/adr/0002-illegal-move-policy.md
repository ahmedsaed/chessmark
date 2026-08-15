# 0002. Illegal moves: full legal list on error, forfeit after 5 retries

**Status:** Accepted
**Date:** 2026-08-15

## Context

Chessmark's headline benchmark question is whether a model can take a valid action in a
tool-mediated environment, every single turn, for 60+ turns. How we handle an invalid action
*defines* what the benchmark measures.

Two failure modes to avoid:

- **Too strict** (instant forfeit): games against weaker models end after five moves. No chess
  signal, boring to watch, and it conflates "can't format a tool call" with "can't play chess".
- **Too lenient** (unlimited retries or auto-correction): illegal moves stop costing anything, so the
  most interesting reliability signal disappears.

## Decision

Three parts:

1. A **`get_legal_moves` tool** is always available. The model can enumerate its options at will,
   any number of times per turn.
2. On a rejected `make_move`, the error result includes the **complete legal move list in SAN**, plus
   a human-readable reason, the current FEN, and the attempts remaining.
3. After **5 failed attempts in one turn**, the agent forfeits with reason `illegal_move_forfeit`.

Illegal attempts do not consume a ply. Provider errors (5xx, timeouts) are retried separately and do
**not** count against this budget.

## Alternatives considered

- **Instant forfeit.** Purest signal, but produces games too short to measure anything else.
- **Unlimited retries.** Cleanest chess-strength signal, but unbounded cost and it discards the
  reliability metric entirely.
- **Random legal fallback.** Every game reaches a chess ending, but a model gets carried by the
  referee — and "moves the engine played for you" is a confusing thing to explain on a leaderboard.

## Consequences

- The benchmark measures *reliability given complete information*, which is the strictly harder and
  more interesting claim. A model that fails here cannot blame ambiguity.
- `illegal_move_rate` and `mean_retries_per_move` become headline metrics, and every attempt is
  recorded, not just the failures that ended a game.
- The retry limit is a tunable constant. It **must** be recorded per game (BENCH-04), because
  changing it invalidates cross-run comparison.
- Providing the full legal move list is a real assist. We should expect illegal-move rates to be
  lower here than in a bare-prompt setup, and must say so on the methodology page.
- Risk: for positions with many legal moves, the error payload is large and re-enters the transcript.
  Watch its effect on context growth and cost.

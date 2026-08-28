# 0020. Threefold and the fifty-move rule are claimed, not applied

**Status:** Accepted
**Date:** 2026-08-28
**Amends:** GAME-04. Bumps `PROMPT_VERSION` to v2 and `TOOL_SCHEMA_VERSION` to v2.

## Context

A pool game between two free models was drawn by threefold repetition at ply 100. Black was
**a queen and a knight up against a bare king** and drew it by chasing the king with checks:

```
46…Qc6+  47.Kf7 Qc7+  48.Ke6 Qc6+  49.Ke7 Qc7+  50.Ke6 Qc6+  ½-½
```

The position after each `…Qc6+` is identical — white king e6, black queen c6, white to move — and it
occurred at plies 92, 96 and 100. Verified independently with `python-chess`: the detection was
correct.

**What was not correct is that nobody had told the model the rule existed.** Three surfaces were
checked and none of them mentioned either draw rule:

| Surface | Threefold | Fifty-move |
| --- | --- | --- |
| System prompt (v1) | absent | absent |
| `get_board` | no repetition count | `halfmove_clock`, unexplained |
| `get_legal_moves`, `get_move_history` | absent | absent |

`halfmove_clock` was the only trace of either, as a bare integer. A model would have to know the FEN
convention, know the threshold is 100 rather than 50, and infer that we apply the rule at all.

There is a second problem underneath the disclosure one. **FIDE makes both of these a claim by the
player having the move** (9.2, 9.3) — precisely because a repetition is usually good for one side and
bad for the other, so deciding for both players is not a neutral default. It takes away a real
choice: a worse side can repeat and claim to save half a point, and a better side can decline. We were
applying it automatically, which converted a resource into an accident.

Two columns, `games.auto_threefold_draw` and `games.auto_fifty_move_draw`, had existed since the
first migration to express exactly this switch. **Nothing had ever read them.** They defaulted to
true and the referee auto-drew regardless of what they said.

## Decision

**The claimable pair is claimed.** A new tool, `claim_draw`, ends the game as a draw when the
position has occurred three times or fifty moves have passed with no capture and no pawn move.
Otherwise it is refused — and the refusal reports *how far off each rule is*, because a bare "no"
teaches a model nothing and it will simply claim again.

**A refused claim is not an illegal move.** It never reaches the illegal-move counter. A claim that
does not apply is a question answered, not a rule broken; charging it would forfeit a model for
asking, which is the mistake `MISSING_PROMOTION` exists to avoid.

**The hard backstops always apply, and are not switchable:** a **fivefold** repetition (9.6.2) and
**seventy-five** moves without progress (9.6.1) draw the game with no claim from anybody. They are
why removing the automatic draw at three cannot make a game loop for ever. Two new terminations,
`fivefold_repetition` and `seventy_five_move_rule`, carry them.

**Both are rated.** A fivefold draw is a real chess result — a model that draws a won game by
shuffling has told us something true about itself. This is not a harness bound (ADR-0019): we did not
impose a ceiling, the rules of chess ended the game.

**The rules are disclosed.** The prompt has a *How the game can end* section naming all four, saying
which are claimable, and stating plainly that a won game can be thrown away by repeating.
`get_board` reports `repetition_count` and `plies_until_fifty_move_draw` — named for the consequence,
because the counter is what nobody read.

**`auto_threefold_draw` and `auto_fifty_move_draw` now do what they say**, read by
`rebuild_referee`, and default to **off**.

## Alternatives considered

**Disclose the rules and keep the automatic draw.** This fixes the unfairness — the model would know
— and is a much smaller change. Rejected because it still removes a legitimate choice from both
players, and because the choice is itself worth measuring: noticing you are in a repetition, and
deciding whether that is good for you, is part of playing chess.

**Draw automatically at five and never offer a claim.** Simpler still, and a losing side then has no
way to secure the half point it is entitled to under the rules. It would also make the harness's
behaviour differ from chess in a way a strong player would notice.

## Consequences

**The ranked record starts again at v2.** Ratings are computed for the current prompt version only
(`bench/ratable.judge`), so every v1 game leaves the leaderboard rather than mixing in. That is the
point of versioning it: a rating across two different prompts measures neither. Acceptable here
because there were eight finished games, five of which had already been found to carry verdicts
neither model earned (ADR-0019).

**A game in progress across the deploy keeps v1 rules but sees the v2 tool list.** The system prompt
is stored per game, so it does not change; the tool schemas are generated from code, so an in-flight
game is suddenly offered `claim_draw`. That costs one cache miss and is otherwise harmless — its
`auto_*` flags are still true, so it draws at three as before, and the extra tool is redundant rather
than contradictory. The migration deliberately does **not** backfill those flags: turning the
automatic draw off for a game that has no `claim_draw` in its stored surface would leave it able to
end only at the fivefold backstop, which is the worst of both.

**Games can now be longer.** A repetition that used to end a game at three no longer does, so a pool
should expect more games reaching the ply cap. The cap is a cost bound and is unchanged.

**`resume --unclaimed-draw` reopens the games this invalidated**, and only those: the termination must
be one of the claimable pair *and* the game must predate the prompt that disclosed the rule. The
general refusal still stands — a chess result is final, and a script that can reopen any draw is a
script that can replay a bad result until it improves.

**A second hand-maintained tool list was found while doing this.** `_available_tool_names` restated
the surface for the unknown-tool error message and was not updated, so a model would have been told
that `claim_draw` did not exist. `ToolName` is a `StrEnum` now and both the test and that message
derive from it.

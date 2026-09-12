# 0038. A prompt version has two parts, and only one of them invalidates a result

**Status:** Accepted
**Date:** 2026-09-13
**Amends:** BENCH-04's rule that a game played under an older prompt is excluded, which was right
about v1 → v2 and too blunt to survive a second change. Extends
[0020](0020-claimable-draws.md), the change that made the first bump necessary.

## Context

`PROMPT_VERSION` exists because a rating is meaningless unless we can say what task produced it.
v1 → v2 earned that: the automatic draw rules were added to the prompt, and before it a model could
lose half a point to a rule it had never been told existed. A rating computed across that boundary
describes neither side of it, so the v1 games left the board. That was correct.

It is also all-or-nothing, and the next change is not the same kind of change.

The turn prompt said *"It is your move. Ply 30."* — not which colour, not what the opponent had
just played. Both facts were reachable only through the system prompt a hundred thousand tokens
back, or through a tool call. So models paid for a tool call: across one real 40-ply game
`get_board` was the **first call in 37 of 40 turns**, and models were observed announcing the wrong
colour and correcting themselves off the board.

Putting the colour and the last move in the turn prompt fixes that. Under the old rule it would
also discard **68 counted games** — every result the project has — to record a distinction that is
not there.

## Decision

**`PROMPT_VERSION` is `major.minor`, and ratings span a minor bump.**

    v2   → v2.1     the same task, stated more conveniently   — older games stay
    v2.1 → v3       a different task                          — older games leave

The test for *minor* is narrow and stated as a question about information:

> Does the change tell a model anything it could not have obtained, in the same turn, through a
> free read-only tool?

The colour is in `get_board`'s side-to-move. The last move is in `get_move_history`. Neither is new
information, so no model's result is now achievable by a model that could not have achieved it
before. What changed is how many calls it takes to be told, and the leaderboard does not report
that.

**The test is necessary and not sufficient**, and the FEN is why. `get_board` returns it, so the
question above would call handing it over "minor" — and it plainly is not. Holding a board across
eighty moves is part of what this benchmark measures, and a FEN in every turn prompt deletes that.
So the second half of the rule is a judgement: *does the change remove work the benchmark is
about?* The colour does not; nobody claims remembering your own seat is the subject. The position
does.

**That judgement is made once per change, in an ADR, with its reasoning written down.** The code
honours the decision; it does not make it. `bench.ratable.same_task` compares majors and nothing
more.

**Every game keeps its exact version.** `games.prompt_version` still records `v2` or `v2.1`, so a
reader can always separate them and an exclusion can always be checked. The minor part decides
nothing except whether a rating may span it.

## Consequences

The 68 games played under v2 keep counting, which is the point. A model rated across v2 and v2.1
has a rating built from games it could have played identically under either.

**The honest risk is that "minor" is always the more convenient answer.** There is no mechanism
here that stops a future change being waved through as minor to avoid a reset; the only guard is
that the reasoning has to be written in an ADR where it can be read and disagreed with. That is
weaker than a rule and stronger than nothing, and it is the same bargain every other judgement in
`ratable.py` already makes.

A reader comparing two models across the boundary is told nothing by the leaderboard about which
version each game used. The drill-down reaches every game (BENCH-02) and each one carries its
version, so the answer is available; it is not on the face of the board. If a minor bump ever turns
out to have mattered, that is where it would be found.

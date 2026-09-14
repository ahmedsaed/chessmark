# 0042. The notation was still analysing the position, and nothing pinned a pool to a task

**Status:** Accepted
**Date:** 2026-09-14
**Amends:** [0040](0040-what-a-board-shows-and-what-the-prompt-owes-you.md), which removed the
`check` and `checkmate` flags and left the same facts in the SAN string. Closes the gap that ADR
recorded in ROADMAP's *Known gaps*. Bumps `TOOL_SCHEMA_VERSION` to **v4**.

## Context

### The flag went and the fact stayed

ADR-0040 took `check` and `checkmate` out of `get_legal_moves`, on the principle that a board client
shows you legal destinations and captures and **does not tell you which move is mate**. It left
`san` alone. `board.san()` returns standard algebraic notation, and standard algebraic notation
carries `+` for check and `#` for mate.

From `1cbf3a36`, the first night v3 ran:

```
get_legal_moves →  ['Bd2', 'Bd3', ..., 'Qe6+', 'Qxc6', 'Qxf7#', 'Rb1', ..., 'h4']
make_move       →  {"move": "Qxf7#"}
game_ended      →  checkmate 1-0
```

Forty-five moves, sorted alphabetically, exactly one `#` among them. That is **worse than the flag
it replaced**: a structured boolean is something a model has to read and reason about, and a `#` in
a sorted list is a single character to pattern-match. The ADR claimed the analysis was gone and it
was not.

### And there were two doors, not one

ADR-0002 answers an illegal move with the full legal move list, so a model can always recover. That
list is the same unstripped SAN, on a different path — `board.legal_moves_san()` →
`IllegalMoveError.as_dict()` → the tool result. Stripping `get_legal_moves` alone would have left a
model able to read the mate off the board **by playing something illegal first**.

### Fixing it is a task change that no prompt version can describe

Removing information from a model's view is major by ADR-0038. But not a word of the prompt changes
— only a tool's *output* does, and `bench.ratable.judge` reads `prompt_version` and nothing else.
`TOOL_SCHEMA_VERSION` has been recorded on every game since the registry existed and **never read**.

It stayed harmless only because every previous tool change happened to move `PROMPT_VERSION`
alongside it. ADR-0040's *Consequences* said so, and said it "will not stay harmless." It was
harmless for eighteen hours.

### Nothing pinned a pool to a task either

`Tournament` recorded neither version. A tournament's crosstable counts every settled game; the
leaderboard excludes by version; the two already disagreed. Worse, a **deploy silently changed what
a running pool was measuring**: when v3 shipped, `pool-free` carried on pairing under a new prompt
into a v2 table, and `pool-free-v3` settled three games under a tool surface that named the mate.
Each time, the repair was to notice by hand and create a new pool.

## Decision

**`get_legal_moves` returns SAN without its suffixes**, through `game.plain_san` — the function that
already existed as `_normalise_san`, because `parse` has always accepted `Nc3` for `Nc3#`. A suffix
is a courtesy annotation, not part of a move's identity, and `uci` sits beside every entry.

**The illegal-move list goes through the same function.** One `_offered_moves()` on the dispatcher,
used by both rejection paths, so ADR-0002's recovery route cannot become a way around ADR-0040's
disclosure line.

**`get_move_history` keeps its marks.** A scoresheet records what happened; it is not an analysis of
the position in front of you. So does the human-play route — a person looking at a board can see
check anyway, and the disclosure rule is about what we hand a *model*.

**`judge` reads the tool schema version**, through the same `same_task` major/minor rule as the
prompt. The prompt is half the task and the tools are the other half; a game matching one and not
the other measured something else and used to count.

**A tournament records the task it opened on**, and holds rather than starting new games when the
deployed versions are no longer `same_task`. Settling is unaffected, so games in flight finish and
score normally. Held rather than rolled over because creating an event is an operator's decision —
its field, its concurrency, its budget — so the right behaviour is to stop and say why, naming the
versions and telling the operator to create a new event.

**Unpinned never holds, and the migration does not backfill.** Every event predating the column
carries `NULL`. Stamping today's versions onto them would be a guess, and a wrong one for precisely
the events that matter: `pool-free` opened on v2 and `pool-free-v3` on a superseded tool surface.
Writing today's version there would assert they measure today's task, which is the claim this column
exists to stop being made by accident.

## Alternatives considered

**Leave the `#`, since SAN is standard.** It is standard *notation*; it is not standard to hand
somebody a pre-computed list of which of their forty-five options wins. The game above shows what it
is worth in practice.

**Strip it in `game/board.py` instead.** Then `LegalMove.san` is no longer SAN, and the domain lies
to every caller — including the human UI and the PGN writer — to serve a policy that belongs to the
agent surface. `game/` describes chess; `agents/` decides what a model is told.

**Bump `PROMPT_VERSION` to v4 with unchanged prompt text.** The one lever `judge` already read, and
it works today. Rejected: it makes the field a task version wearing a prompt version's name, and the
next person to read `prompt_version: v4` on a game whose prompt is identical to v3's would be right
to be confused.

**Let the three v3 games mix.** Cheapest, and it sets the precedent of ignoring a known gap the
first time it bites.

**Roll a stale pool over automatically** — abandon it and open a successor with the same field.
Tempting, and it makes a deploy quietly end an event, which is a larger thing to do without being
asked than stopping one is.

## Consequences

**The board clears again**, three games after the last time. `pool-free-v3` is abandoned and
`pool-free-v4` opened; the v3 games stay readable and stop counting on the tool version.

**Two pools now hold instead of playing**, and will say so in the runner's log — both are already
retired or about to be, so nothing is lost. Anything created from now on stops on its own when the
task moves, which is the point.

**A model that quotes `Nc3#` back is still understood.** `parse` normalises before matching, so
nothing a model has learned stops working; it simply is not told.

**Expect this to hold a pool at an inconvenient moment.** That is the trade: a table that measures
one task, at the cost of a deploy occasionally stopping an event until somebody creates the next
one. The alternative is the silence we have had three times.

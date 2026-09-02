# 0026. A repeated question gets a different answer

**Status:** Accepted
**Date:** 2026-09-02
**Amends** [0019](0019-harness-bounds-are-not-findings.md) — it drew the line between our ceilings
and a model's failures; this is about not walking a model into one of them for nothing.
Implements AGENT-21.

## Context

In game `855e208d`, `nemotron-3-super-120b` called `get_move_history` with no arguments **twenty
times in one turn**. Every call returned a byte-identical result. Every response carried the same
2,303 tokens of reasoning. On the twentieth it hit `max_tool_iterations` and lost the game to
`error_forfeit`.

The exchange was deterministic, which is why nothing broke it: identical messages in, identical
reasoning out, identical call, identical result. Handing back the same answer a third time is the
one response guaranteed to produce the same question again.

It cost **1.96 million prompt tokens** and twenty of the account's thousand daily free requests, for
one ply that never happened — on a benchmark whose scarcest resource is requests.

There is a real question about whether the forfeit was fair. `max_tool_iterations` is a ceiling the
harness imposes, and ADR-0019 says a harness bound fails the turn rather than forfeiting the player;
`bench/ratable.py` nonetheless rates `ERROR_FORFEIT` on the stated grounds that agentic reliability
*is* the measurement. Both readings are defensible. What is not defensible is that the harness
watched a model spin nineteen times and never told it.

## Decision

**A read-only tool called a third time with the same arguments in one turn is answered with a
nudge, not with the answer.** The payload says how many times it has asked, that nothing changes
until it moves, that the answer is already in the conversation, and **how many tool rounds it has
left before it forfeits** — a model that cannot see it is running out cannot act on it.

Three details are load-bearing:

- **Read-only tools only.** A repeated `make_move` is an illegal-move retry and belongs to
  ADR-0002, which already answers it with the full legal move list five times over. Intercepting it
  here would take a rule from the module that owns it and quietly cut the retries short.
- **The signature is the name and the arguments together**, so reading the board, listing the moves
  and checking the history is three questions rather than a repeat. Keyed on the name alone, this
  would have refused the ordinary way to play a turn.
- **Two are free.** A model just refused an illegal move may reasonably re-read the board, and the
  position genuinely has not changed. What it may not do is ask a third time and expect something
  different.

**This is not a reprieve, and it is not retroactive.** A model that spins to the ceiling anyway
still forfeits, and the games already lost that way stay lost. Both seats ran the same harness
under the same rules, and a model that cannot operate its tools is precisely the finding the
benchmark exists to publish. Re-adjudicating a settled result because the harness later became more
helpful would make the leaderboard a record of our tooling rather than of the models.

## Alternatives considered

**Return the real result with a warning attached.** The gentler version, and it fails on the
mechanism: the loop is deterministic because the answer does not change, so an answer that still
contains the answer is likely to produce the same call. The result must actually differ.

**Stop rating `ERROR_FORFEIT` from `max_tool_iterations`.** This is the ADR-0019 argument taken to
its conclusion, and it was rejected by the owner on the merits: a model given twenty rounds, a full
tool surface and an unchanging position that still never moves has failed the task, and hiding that
leaves a leaderboard measuring only chess.

**Detect the loop and end the turn early rather than nudging.** Cheaper — it stops the spending
sooner — but it decides the model cannot recover without giving it the one piece of information it
was missing. If it moves after the nudge, the game continues, which is the outcome worth buying.

## Consequences

**The tool *schema* is unchanged**, so the cacheable prefix is untouched (invariant 2) and this is
not a new tool surface or a prompt-version bump. Only a result payload differs, and only after the
model has already had the same answer twice.

**A model that legitimately needs a third identical read is refused.** No such model is known —
nothing about a position changes mid-turn, and the previous two answers are in the transcript — but
this is the assumption to revisit if a model starts forfeiting shortly after a `repeated_call`.

**The declined call is still logged verbatim** (invariant 3), with `repeat` in the event payload, so
the record shows both that the model asked and that we chose not to answer. A future audit can count
how often the nudge fires and whether it works, which is the number that decides if two is the right
allowance.

# 0029. A rating deviation has a ceiling

**Status:** Accepted
**Date:** 2026-09-02
**Amends** [0028](0028-a-wider-prior-and-a-provisional-mark.md), which recorded this gap in its
consequences rather than closing it. Implements BENCH-12.

## Context

`Glicko2._decay` widens a contestant's deviation every rating period in which they play nothing:
`phi' = sqrt(phi² + sigma²)`. That is right and load-bearing — a rating from March is not still
worth ± 40 in December — and nothing bounded it.

Glickman's system holds that a rating deviation should never exceed the initial one. The reason is
what a deviation *means*: it is our uncertainty about a contestant, and there is no state of
knowledge worse than never having seen them. A model idle for years is no better known than a new
one, and no worse.

**The measured size of the gap, since ADR-0028 stated it loosely.** From ± 60 at σ = 0.06, an
uncatched decay needs about **2,270 idle daily periods — a little over six years** — to pass 500.
Raising the prior in ADR-0028 pushed that further out, not nearer; at the old 350 it was about
1,350 periods. So this was never close to biting, and the first version of the test written for it
asserted something that passed with the cap removed.

It is still worth closing. The leaderboard is meant to outlive its first year, an unbounded number
is one nobody can reason about, and the rule is the rating system's own rather than a preference.

## Decision

**A deviation is capped at `MAX_RD`, which is an alias for `DEFAULT_RD` rather than its own
literal.** They must not drift: a ceiling below the prior would clamp every new contestant on their
first period, and one above it would let an absence make a model less known than a stranger.

**The cap is applied to `phi_star` — the deviation a period *starts* from — in both paths through
`rate`**, via one `_phi_star` helper. That is where Glickman puts it, and the placement matters:
capping the *output* instead would weigh the period's games against an unbounded prior and then
report a bounded number, so the deviation shown and the rating computed from it would disagree.

A period with games still shrinks the result further, so a long-idle contestant can be measured
again. A cap that stopped that would be a trap rather than a bound.

## Alternatives considered

**Clamp inside `Rating.from_internal`**, catching every construction. Rejected: it would silently
rewrite values a caller passed deliberately — including the worked example's fixtures — and a
constructor that quietly disagrees with its arguments is worse than an uncapped decay.

**Leave it, and record the bound in the docstring.** What ADR-0028 did. The owner's answer is the
right one: a rule stated and not enforced is a rule that will be broken by whoever reads the code
next and reasonably assumes the docstring describes the behaviour.

## Consequences

**No visible change today, and none for years.** Nothing in the live data is near the ceiling — the
widest deviation on the leaderboard is 265 — so this changes no current number. It is enforcement of
a rule that was already meant to hold.

**Two of the three new tests pass with the cap removed**, and say so in their docstrings. They pin
what the cap must *not* break — early clamping, and a capped contestant becoming unmeasurable — which
is the failure a ceiling makes easy. Only the eleven-year idle test demonstrates the bug, and it
needs that long because six years of decay is what it takes to reach the bound.

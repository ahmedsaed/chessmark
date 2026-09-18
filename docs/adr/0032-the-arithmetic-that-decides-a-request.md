# 0032. The arithmetic that decides whether a request can be sent — moved

**Status:** Renumbered
**Moved to:** [0047](0047-the-arithmetic-that-decides-a-request.md)

This decision was written as 0032 on 2026-09-06, the same day as
[0032. The leaderboard is stored, not recomputed on every request](0032-the-leaderboard-is-stored-not-recomputed-per-request.md).
Two unrelated decisions held one number, and only the leaderboard one reached the index.

The collision was not harmless. `CHANGELOG.md` defines a single `[ADR-0032]` link reference, so a
line about the leaderboard's cost sent readers to the context arithmetic; `agents/llm.py` cited
0032 meaning this decision while `bench/snapshot.py` cited 0032 meaning the other. A citation of
"ADR-0032" carried no information at all.

**The content moved unchanged to [ADR-0047](0047-the-arithmetic-that-decides-a-request.md).** This
file stays so that links written against the old path still land somewhere true — deleting it would
turn every existing citation into a 404, which is the cost the renumber was supposed to avoid.

An ADR is immutable, and this is the narrow exception the rule did not anticipate: not a decision
that changed, but a filing error in which number it was given. Nothing about what was decided, why,
or what it costs is different. `make check` now fails on a duplicate ADR number, so this cannot
happen again — see `docs/adr/README.md`.

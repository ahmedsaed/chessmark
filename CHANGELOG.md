# Changelog

Notable changes, newest first. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Chessmark is in beta.** That is what `0.x` means under SemVer — *"anything MAY change at any
time; the public API SHOULD NOT be considered stable"* — so the version carries it and the tags do
not repeat it. Releases are published normally rather than marked pre-release: there is no stabler
version to point people at, and a repository whose every release is a pre-release advertises no
current version at all.

This file starts at `0.1.0`. Everything before it is in the git history and, where it was a
decision rather than a change, in [docs/adr](docs/adr/) — an ADR is the record of *why*, and this
file is only the record of *what shipped when*.

## [0.1.0] — 2026-09-01

First tagged release. Three fixes found by reading the live `pool-free` event.

### Fixed

- **The pool no longer re-pairs a fixture it can never play.** `gemma-4-26b` v `gemma-4-31b` was
  scheduled seven times over five days without a move being played: an abandoned pairing carries no
  score — deliberately, since it must never be *scored* — and the matchmaker read that absence as
  "these two have never met". Attempted pairings now count as meetings. An entrant whose last two
  finished pairings both came to nothing also rests for six hours, without which the first half only
  sends a model that cannot play hunting a fresh opponent. (OPS-21)
- **An endpoint's output ceiling is no longer a finding about a model** ([ADR-0024]). We asked
  Poolside for 64,000 output tokens against an endpoint that stops at 32,768, so every truncation
  stopped *short* of our request — the exact signature the harness read as the model's own failure,
  which cost `laguna-s-2.1` a game it had won by rook and two bishops against a lone pawn. Requests
  are now clamped to the endpoint's `max_completion_tokens`, and `TRUNCATED` is a harness stop
  rather than a rated forfeit.
- **A forfeit flag follows the game's ending, not the turn's status** ([ADR-0024]).
  `BUDGET_EXCEEDED` ends a game, so it travelled as a forfeit and set a published flag; two games
  were budget-stopped, reopened, and played on to a genuine checkmate and a genuine threefold draw
  while still carrying a forfeit on the leaderboard. `resume` now clears a stale flag too.
- **The version is read from the packaged distribution**, not written in three places.
  `api/routes/health.py`, `main.py`'s OpenAPI metadata and `pyproject.toml` each held their own
  literal, tied together only by two tests asserting the string `"0.1.0"` — which had to be edited
  on every bump, so the failure always read as "the test is stale" rather than "the API is
  reporting a version it is not". The tests now assert that `/health` and `/openapi.json` agree
  with the installed distribution.

### Added

- `./chessmark repair-forfeits` — reconciles seats' forfeit flags against the game record, which is
  the authority (invariant 1). Reports by default, changes no result and reopens no game.

### Removed

- `./chessmark resume --harness-ceiling`. With `TRUNCATED` resumable outright the flag could never
  fire again, and a gate that cannot fire implies a distinction no longer being drawn.

### Notes

Ratings recompute from each game's termination, so historical truncations leave the rated set on
deploy — no backfill and no migration. Run `./chessmark repair-forfeits` after deploying to clear
flags the old code wrote.

[ADR-0024]: docs/adr/0024-endpoint-output-ceilings-are-not-findings.md
[0.1.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.1.0

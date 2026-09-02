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

## [Unreleased]

Three fixes from an audit of the live `pool-free` event, which had abandoned 21 of 56 pairings.

### Fixed

- **A game due to resume keeps its concurrency slot** ([ADR-0025]). The tournament runner bounded
  itself on running games only, so a pairing whose pause had expired was invisible to it and
  visible to the reconciler; the two raced for one slot and the waiting game lost. Measured across
  the five deepest abandoned games, **48–99% of every pause was our own queue** rather than the
  provider's — one game was asked to wait sixty seconds and sat for 16.4 hours. (OPS-22)
- **Patience is measured from a game's last move, not its first pause** ([ADR-0025]). The window
  claimed to mean "cannot get a turn in a day" and actually meant "has been pausing on and off for
  a day", so a game that paused at ply 4 and then played eighty-eight more moves was abandoned
  anyway. Three games died at plies 71, 68 and 56 having never gone more than 17.4 hours without
  moving, each within seven playing-hours of a real result. (OPS-22)
- **A read-only tool asked the same question three times gets a nudge, not the answer**
  ([ADR-0026]). `nemotron-3-super-120b` called `get_move_history` twenty times in one turn, received
  a byte-identical result and emitted identical reasoning each time, and forfeited on
  `max_tool_iterations` — 1.96M prompt tokens and twenty of the thousand daily free requests for a
  ply that never happened. The nudge says what it has asked, that nothing changes until it moves,
  and how many rounds it has left. Games already lost this way stand: both seats ran the same
  harness. (AGENT-22)

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
[ADR-0025]: docs/adr/0025-finishing-a-game-beats-starting-one.md
[ADR-0026]: docs/adr/0026-a-repeated-question-gets-a-different-answer.md
[0.1.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.1.0

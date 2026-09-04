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

Two audits of the live `pool-free` event. The first found three rating problems; the second
followed 17 abandoned pairings of 65 back to a turn loop that was refilling its own context faster
than compaction could empty it.

### Fixed

- **A turn may no longer inflate its own context** ([ADR-0031]). A reply cut off by the endpoint
  before it reached a tool call is now elided from the *replayed* transcript — the message keeps
  its place, role and structure, and carries a placeholder instead of the fragment. Measured on
  turn 1041 of `29e7f004`: compaction ran at the top of the turn and left a 731-token prompt, and
  the same turn put it back to **516,877** across ten calls, five of them cut off at the
  endpoint's undeclared 32,768-token ceiling and each appended in full. Every failed attempt made
  the next one harder. The row is untouched and `llm_calls` still holds the raw response, so the
  record stays verbatim (invariant 3); only the request shrinks. A reply truncated *after* it
  managed a tool call is kept whole, because that one may carry reasoning its provider requires.
- **A compaction keeps its measurement instead of discarding it** ([ADR-0031]). `_compact` set the
  measured prompt size to `None`, which sent the next request down the unmeasured path — a bound of
  half the window, sized for a game's genuine first call. On a resumed game holding 227,440 tokens
  that asked for 64,000 output against 27,802 tokens of room, and the endpoint refused it. The
  reset was persisted too, so a reopened game began blind and could not even compact its way out;
  `29e7f004` and `e601f9af` each re-threw the same 400 within seconds of being reopened, twice.
- **Compaction may fire again when the transcript has grown since the last fold** ([ADR-0031]).
  The guard was once per turn, which is right for a turn making three round-trips and useless for
  one making twenty.
- **A summary that hits its own cap is discarded** ([ADR-0031]). Fifteen summarising calls across
  the pool returned `finish_reason: "length"` at 2,000 tokens, eight from one model — each writing
  a sentence that stops mid-word into the transcript as that game's memory of itself. The pass
  falls back to the trim-only rung, which needs no provider at all.
- **Reopening an abandoned game restarts its patience window** ([ADR-0031]). The 24-hour clock ran
  from the last move alone, which also counted every hour the game spent dead and every hour it
  spent queued. `b5546c1b` was reopened 32 hours after its last move, ran 6.7 real hours, met one
  rate limit and was abandoned again at "38.9h without a move". Only a deliberate reopening resets
  it — an expiring pause writes a different event — so a failing provider still reaches 24 hours
  exactly as intended. The abandonment message now names the instant it counted from.
- **An entrant mid-pairing is not given a second concurrent game** ([ADR-0031]). A paused game
  holds no concurrency slot, so the freed slot went straight back to a matchmaker that still saw
  the same least-known entrant — and `_fruitless_entrants` counts only *finished* attempts, which
  a failure here takes a full day to become. `gemma-4-26b` held four games inside nine hours, all
  scheduled before the first had ended, all four abandoned.
- **Each pooled game gets its own round number.** A batch carried one number for every game in it,
  which is right for a Swiss round and wrong for a pool, where each game is matched independently
  against ratings the previous one has already moved. `pool-free` showed 63 rounds of one game and
  a single round of two.
- **A game abandoned before its first move shows its log.** `eventsThroughPly` returned nothing at
  ply 0, so a zero-ply game folded an empty list and rendered "The starting position — step forward
  to begin." over a pairing that never began. Five games in `pool-free`, every one with its reason
  recorded and none of it on the page.
- **The event stream says when a game ended, and why.** `game_ended` was the one lifecycle event
  that pushed no notice, so a log ran pause → resume → pause → resume and stopped. On a game
  reopened and then abandoned again, the last thing a reader saw was "resumed".
- **A reasoning bubble is bounded.** Degenerate output — three thousand characters of multilingual
  noise and eighty consecutive newlines, from a model whose serving stack collapsed mid-game —
  rendered at full height and pushed the rest of the conversation out of the column.
- **The event log never loses its ending.** `/games/{id}/events` takes rows from the front, which
  replay needs, so a game longer than the cap dropped its tail. `a59a388e` already emits 1,093
  events over 147 plies.

### Changed

- **`context_exceeded` is a harness ending, not a forfeit** ([ADR-0031]). While the agent had no
  way to shrink its own history, filling the window was something the model did; now that it folds
  its history when the window fills, reaching the wall says our fold did not keep up. It leaves
  the rated set and becomes resumable, following `timeout` and `truncated`.


- **A new contestant starts at 1500 ± 500, and a rating above ± 110 is marked provisional**
  ([ADR-0028]). Both numbers are Lichess's, adopted verbatim rather than tuned. The wider prior
  suits a field the matchmaker keeps refreshing — it pairs whoever is *least* known, so most games
  are spent on models that have barely played, and Glickman's 350 is calibrated for the opposite
  population. The mark is the deviation said in a word; today it applies to **every** contestant,
  at ± 150 to ± 265 over two to nine games each, which is the honest thing for the page to say.
  Provisional never reorders anything, and an *unrated* entrant is not marked provisional. Ratings
  are recomputed from the games on every request, so this needs no migration. (BENCH-12)
- **A rating deviation is capped at the prior it started from** ([ADR-0029]). `_decay` widened it
  every idle rating period and nothing bounded it, so it could pass the deviation we give a model
  nobody has ever seen — and there is no state of knowledge worse than that. Measured, the breach
  needed about 2,270 idle daily periods (six years), so nothing was close to it; the cap is
  Glickman's own rule and the leaderboard is meant to outlive its first year. Applied to the
  deviation a period *starts* from, so games still shrink it and a long-idle model stays
  measurable. (BENCH-12)
- **A pool's standings are ranked by a rating computed over that pool's games** ([ADR-0027]), with
  the deviation as the tiebreak; a closed event still ranks by score, Sonneborn-Berger and direct
  encounter. A pool has no fixed schedule, so its entrants finish unequal numbers of games and a sum
  of points partly measures how many they were handed — in `pool-free`, two models that had won
  every game they played stood third and fourth behind one that had lost a game in eight. Points and
  W/D/L stay on the page; they no longer decide the order. The rating is that pool's own, so a place
  cannot move because of a game played elsewhere, and the eligibility rules are unchanged by the
  scope. An entrant with no ratable game reads `unrated` and sorts last, never 1500. (BENCH-11)

### Added

- **The model catalogue now refreshes itself** (OPS-23). `refresh_catalogue.py` was written to be
  scheduled — its own header says so — and nothing scheduled it: no cron container, no timer, no
  workflow, no call from the API, the worker or the tournament runner. It ran when somebody
  remembered the command. Prices set the spend caps *and* what a user is charged in credits
  (ADR-0016) and endpoint rows are what the picker pins to, so every day nobody remembered was a
  day of wrong caps, wrong prices and models the field could not play. A `catalogue` service now
  runs it at start-up and every `CATALOGUE_INTERVAL_HOURS` (12); a failed pass is logged and the
  loop waits, because a scheduled sweep that exits on a bad night is restarted straight back into
  it. `--every HOURS` is on the script, so `make refresh-catalogue` and `./chessmark catalogue`
  still run one pass and still fail loudly. It spends nothing — `/models` and `/endpoints` are
  metadata, not inference.

- **A tournament's schedule shows the latest ten matches and loads more on request.** A pool never
  ends, so its schedule only grows — and it was rendered whole, several hundred linked rows in one
  column, under the standings table that is the reason most people open the page. The order was
  already newest-round-first, so the first page is the part worth seeing. Every pairing is already
  on the page, so "load more" is a slice rather than a request: nothing to wait for and nothing to
  fail. The count says `10 of 312` while there is more, and the button says how much is left.

- **`tournament set <slug> --max-concurrent N`** changes a running event's concurrency without a
  hand-written `UPDATE`. It was settable only at `create`, which is the one moment nobody knows the
  right value — it depends on how many workers are up, how hot the free pools are that day, and how
  long a turn is taking. Takes effect on the next tick; refuses zero, which is a pause that does not
  say it is one. Omit the value to print the current setting. (OPS-22)

### Fixed

- **The reconciler reads the halt's scope instead of standing down for any halt** ([ADR-0030]).
  It asked `halt.active()` once and returned, which was right while a halt was global and wrong the
  moment it had one: under OpenRouter's daily free-model cap **no paid game could be rescued at
  all** — not resumed when its provider pause came due, not requeued when its job was lost — for as
  long as the cap stood, up to a UTC day. The worker and the tournament runner both read the scope
  correctly; nothing had ever passed a halt to `reconcile`, so nothing could have caught it. A
  paused game the halt covers is now held (resuming it would take a concurrency slot and pause
  again having moved nothing) and a *stalled* one is requeued anyway, because the worker answers
  that job by writing the pause below. (OPS-19, OPS-20)
- **A halt now pauses the board instead of stopping it silently** ([ADR-0030]). A turn the global
  halt forbade was dropped and the game left `RUNNING` — correct about the record and invisible on
  the page: the header went on pulsing **live** over a board that would not move again until the
  free-model allowance reset, which is most of a UTC day. Every game a halt covers is now paused
  with one `game_paused` event carrying the reason and, where the halt knows it, the time it lifts.
  Nothing is forfeited and no abandonment clock runs — a halt is ours, not the model's
  (ADR-0019) — and a paid seat is still untouched by a free-model cap. (OPS-19, OPS-20)
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
[ADR-0027]: docs/adr/0027-a-pool-is-ranked-by-its-own-rating.md
[ADR-0028]: docs/adr/0028-a-wider-prior-and-a-provisional-mark.md
[ADR-0029]: docs/adr/0029-a-deviation-has-a-ceiling.md
[ADR-0030]: docs/adr/0030-a-halt-pauses-the-board.md
[ADR-0031]: docs/adr/0031-a-turn-may-not-inflate-its-own-context.md
[0.1.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.1.0

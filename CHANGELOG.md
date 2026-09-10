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

### Added

- **A turn streams as it happens** ([ADR-0035]). A turn is one transaction, so it published
  everything at the end: ply 8 of `e601f9af` spent **632 seconds** across six provider rounds
  (1.1s, 10.9s, 29s, 220s, **369s**, 2.8s) and delivered all fifteen of its events stamped the same
  millisecond. Rounds are now announced as they finish, on a channel that carries no `seq`, is
  never stored, and is superseded by the committed events — so the record is byte-identical whether
  anyone was watching or not. Measured: the first frame lands a full second ahead of the commit on
  a turn with two half-second rounds.
- **Reasoning streams token by token** ([ADR-0035], [ADR-0036]). LiteLLM's streaming path reads
  `reasoning_content` and drops `reasoning`, so on some providers the thinking would never arrive —
  and an absent reasoning field is indistinguishable from a model that did not reason, which is
  what made this the one invariant-3 breach nothing downstream could flag. It *is* distinguishable
  from a **billed** one: a response reporting `reasoning_tokens > 0` and carrying no reasoning text
  is one where the text existed, was paid for, and was not collected. That endpoint goes back to
  whole responses on the spot, so the cost of learning it is one call. On by default;
  `LLM_STREAM=false` stops asking providers to stream at all.


One model was described by two pages, and the numbers on them disagreed.

### Changed

- **A model has one page** ([ADR-0034]). `/models/{slug}` and `/leaderboard/{slug}?q=` both headed
  a panel `W / D / L` — one over every game, one over the ratable ones — and neither said so, so
  which pair a reader saw depended on whether they arrived from the leaderboard or from the
  tournament table. The contestant is a block on the model page now, one per precision, carrying
  its rating and the ratable games behind it (BENCH-02). The old URL redirects, `?q=fp8` becoming
  `#c-fp8`.
- **The games that did not count are listed with the reason** ([ADR-0034], BENCH-10). The
  difference between the two figures, itemised and grouped, on the page that prints both. Two
  W/D/L figures are honest only if a reader can see what separates them.

### Fixed

- **The two wall-clock latency tests are deleted** (NFR-01, NFR-02). They held an in-process p95
  against a fixed budget, so what they measured was the machine running them: a run with a **5.7 ms
  median** and a **329 ms p95** failed a pull request that changed no Python. It was the third time
  that one assertion had been wrong, and a check a rerun clears is not a check — it teaches people
  to rerun CI, which the next real regression is then rerun away too. What they stood in for, an
  N+1 or a sync call on the hot path, is caught deterministically by the read path's query-count
  tests. The requirements stand and are for a real load test; ROADMAP's *Known gaps* records that
  they have no automated check. The fanout test, which asserts a fact rather than a duration,
  stays — `test_performance.py` is now `test_fanout.py`.
- **A model page asked for its games under a name no model has** (UI-07). A dynamic route segment
  arrives percent-encoded, so `google/gemma-4-31b-it:free` reached the page as
  `gemma-4-31b-it%3Afree`. In a *path* that still works — the server decodes it again — so the
  page rendered with the right name and the right stats; in a *query value* `URLSearchParams`
  escaped the `%` and the API looked for a model literally called `…%3Afree`, found none, and
  answered **`200 []`**. Nothing threw, nothing was logged, and every `:free` model — nearly the
  whole catalogue — looked like a model that had never played. It had been hidden because the
  drill-down fetched its games through a path; folding that page in ([ADR-0034]) exposed it. The
  id now comes from the registry's own answer, and a page that has stats but no games says so
  instead of rendering as empty.
- **A leaderboard row reaches only half its games** (BENCH-02). The drill-down's index recorded
  each counted game under its *first* seat, and seats are read ordered by colour — so black took
  every game and white got none. `ling-3.0-flash-fin` showed `6 / 5 / 4` and listed eight games:
  the W/D/L counts come from a different pass over the same scan and stayed correct, which is what
  made a row that could not open half of its own results look like a display fault. Every seat is
  indexed now, and a mirror match still appears once. **This is why the two pages could not be
  reconciled by reading them** — and it took far longer to find than it should have, because the
  product could raise the question at all.
- **A model page cost two full sweeps of the archive**
  ([the stored leaderboard](docs/adr/0032-the-leaderboard-is-stored-not-recomputed-per-request.md);
  linked by path because two ADRs carry the number 0032). `get_model` recomputed the ratings and
  the aggregates per request without sharing a scan — the cost ADR-0032 had just removed from the
  leaderboard, reintroduced on a route nothing measured. It reads the stored run now, and a
  query-count test holds it there.

### Fixed

- **What compaction keeps is bounded by size, not message count** ([ADR-0033]). Measured on two
  real games under the same twelve-message cap: `10fc99f0` kept 12 messages totalling 606,376
  characters — about 210,000 tokens of a 256,000-token window — while `545dc41a` kept 11 totalling
  66,755. The larger filled 82% of its window with the one region compaction may not touch, which
  is the deadlock that left two games unrecoverable: to shrink the conversation the model must
  write a summary, and there is no room to write one *because the conversation is too big*. The
  budget is `KEEP_TAIL_TOKENS = 20,000`, matching what oh-my-pi, Pydantic AI and Hermes protect.
- **A turn that alone exceeds the budget is clamped rather than kept whole** ([ADR-0033]). There is
  no legal cut left at that point — dropping to zero turns leaves nothing to act on, and cutting
  inside a turn orphans a `tool` result — so the largest messages keep their head and their tail
  and lose their middle, marked with how much went. Rendered from `content` on every replay rather
  than stored pre-cut, so the cacheable prefix does not move (invariant 2).
- **An endpoint that misreports its own prompt size ends the game** ([ADR-0033]). A call that
  *succeeded* necessarily fit, so `prompt + the output we asked for` cannot exceed the window —
  and `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` broke that on 24 of 339 calls, worst
  case claiming 516,877 tokens in a 256,000-token window, while eleven other models broke it on
  none of ~7,800. The figure was carried to the next turn and stopped the seat before it made a
  call. There is no honest recovery — no client-side count is trustworthy — so the game is
  abandoned and the endpoint is named. Nobody is forfeited (ADR-0019).

### Changed

- **`players.last_prompt_characters` is recorded beside `last_prompt_tokens`** ([ADR-0033]), both
  counted at the same instant on the same transcript. Their quotient converts our exact character
  counts into that endpoint's tokens, which is what lets a tail budget be expressed in the unit the
  window is in. It sizes a retention policy, never a safety bound: whether a request can be sent is
  still decided by the provider's own count alone (AGENT-19).

Three games survived the ADR-0031 fixes and were traced to three separate pieces of arithmetic.

### Fixed

- **The reactive compaction rung compacts against the prompt, not the whole request**
  ([ADR-0032]). An endpoint's refusal reports a total that includes the `max_tokens` *we* asked it
  to reserve, and passing that on charged our own output request against the transcript a second
  time — leaving `_summarise` with negative room, so it never called anything. `29e7f004` and
  `e601f9af` were reopened three times each and died **one second** after every attempt, never
  having tried to rescue themselves. The endpoint's own breakdown is parsed where it gives one.
- **A truncation is failed on sight only when the request was unanswerable** ([ADR-0032]). The
  rule was "the response reached the number we sent", which made the verdict depend on the
  registry being *wrong*: while the catalogue was stale we asked `laguna-s-2.1` for 64,000, got
  its real 32,768, and read that as the endpoint's limit — worth a nudge and three retries. After
  the catalogue was refreshed the identical response read as ours and failed instantly, abandoning
  `a016a326` at ply 72. What decides now is the size of what we allowed.
- **A rescue that outlives the turn that needed it** ([ADR-0032]). A turn is one transaction, so a
  compaction inside a failing turn is rolled back with it and the next attempt sends the same
  bytes. On a context-length rejection the worker now elides stale tool output in a session of its
  own and requeues once — trim-only, because folding needs the provider that just refused us.

### Changed

- **`FRAMING_TOKENS` is 4,096, not 256** ([ADR-0032]). One thousandth of a 256,000-token window is
  a rounding error against a count taken on the provider's side; comparable agents hold back 4,096.
- **An unmeasured call holds back the reserve rather than half the window** ([ADR-0032]). 25,600
  against 256,000 where the old bound asked for 128,000. Half a window "always fits" for a game's
  genuine first call and not for a resumed one carrying 227,440 tokens, which is how a request for
  64,000 output reached an endpoint with 27,802 tokens of room.

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
[ADR-0032]: docs/adr/0032-the-arithmetic-that-decides-a-request.md
[ADR-0033]: docs/adr/0033-a-tail-budget-in-tokens-and-a-provider-that-cannot-count.md
[ADR-0034]: docs/adr/0034-one-page-per-model.md
[ADR-0035]: docs/adr/0035-live-frames-are-not-events.md
[ADR-0036]: docs/adr/0036-a-lost-reasoning-trace-announces-itself.md
[0.1.0]: https://github.com/ahmedsaed/chessmark/releases/tag/v0.1.0

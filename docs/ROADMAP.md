# Roadmap

## How this is structured

Phases are deliberately **small and individually testable**. Each one has:

- **Goal** — one sentence, what exists at the end that didn't before
- **Objectives** — the work
- **Exit criteria** — *verifiable* conditions. Not "it works", but "this command produces this output"
- **Covers** — requirement IDs from [REQUIREMENTS.md](REQUIREMENTS.md)

**No phase is done until its exit criteria pass and its tests are written.** A phase that "mostly
works" is not done. Deferring a test is deferring the phase.

Phases 1–5 build a fully working benchmark engine with **no UI at all** — every one of them is
testable from a terminal. That ordering is intentional: the hard, novel part is the agent runtime,
and it should be proven before a single pixel is designed.

### Dependency graph

```mermaid
flowchart LR
    P0[0 Foundations] --> P1[1 Chess core]
    P1 --> P2[2 Persistence]
    P2 --> P3[3 LLM gateway]
    P3 --> P4[4 Agent runtime]
    P4 --> P5[5 Orchestration]
    P5 --> P6[6 API + SSE]
    P6 --> P7[7 Spectate UI]
    P7 --> P8[8 Replay + share]
    P6 --> P9[9 Auth + quotas]
    P9 --> P10[10 Human vs model]
    P9 --> P11[11 Moderation]
    P5 --> P12[12 Ratings]
    P12 --> P13[13 Tournaments]
    P5 --> P14[14 Stockfish]
    P4 --> P15[15 Personas]
    P9 --> P16[16 Cost dashboard]
    P8 --> P17[17 Launch]
    P11 --> P17
    P13 --> P17
    P16 --> P17
```

---

## Phase 0 — Foundations ✅ COMPLETE

**Goal:** a developer can clone the repo and have every dependency running in one command.

**Objectives**
1. Monorepo layout, `.gitignore`, `.editorconfig`
2. FastAPI backend via `uv`, with ruff + strict mypy + pytest
3. Next.js 16 + TypeScript + Tailwind 4, with eslint + tsc
4. Docker Compose: Postgres 16, Redis 7, on non-conflicting ports
5. `Makefile` task runner
6. CI running lint, typecheck, tests
7. Research docs: vision, requirements, architecture, roadmap, ADRs

**Exit criteria**
- [x] `make setup && make up` brings up healthy Postgres and Redis
- [x] `make check` passes: ruff, eslint, strict mypy, tsc, pytest
- [x] `GET /health` returns `{"status":"ok","version":"0.1.0"}`
- [ ] CI is green on a push to `main`

**Covers:** OPS-01, OPS-03, OPS-06

---

## Phase 1 — Chess core domain ✅ COMPLETE

**Goal:** a complete, correct chess referee with zero knowledge of LLMs, databases, or HTTP.

**Objectives**
1. `game/board.py` — thin wrapper over `python-chess` exposing FEN, ASCII, legal moves (SAN + UCI + flags), material
2. `game/referee.py` — apply a move, detect every terminal condition, return structured results
3. `game/errors.py` — a structured illegal-move error carrying the full legal move list and a human-readable reason
4. `game/pgn.py` — export to PGN with Chessmark custom tags
5. Move parsing that accepts SAN or UCI and normalises both

**Exit criteria**
- [x] Unit tests cover all of GAME-02: castling both sides, en passant, all four promotion pieces, threefold repetition, 50-move rule, stalemate, insufficient material (all four cases)
- [x] A known PGN of a famous game replays ply-by-ply to the correct final FEN — the Opera Game (Morphy, 1858), 33 plies to `1n1Rkb1r/p4ppp/4q3/4p1B1/4P3/8/PPP2PPP/2K5 b k - 1 17`
- [x] `IllegalMoveError` for every rejection carries a non-empty `legal_moves_san` and a `detail` string
- [x] Test coverage on `game/` > 90% — **99.75%**, enforced in CI via `make cov`
- [x] Module imports nothing from `db/`, `agents/`, or `api/` — enforced by an AST test in `tests/game/test_purity.py`, which also forbids relative imports so the check cannot be sidestepped

**Covers:** GAME-01, GAME-02, GAME-04, GAME-05, GAME-06, GAME-07

**Notes**
- Threefold repetition and the fifty-move rule are applied **automatically**, deviating from FIDE
  where both are *claimable*. A benchmark cannot rely on a model noticing it may claim a draw;
  without this, two weak models shuffle until the ply cap. Documented in `referee.py`.
- Terminations are split into chess results and `FORFEIT_TERMINATIONS` (illegal-move, error,
  timeout, budget, context), so the leaderboard can separate "lost at chess" from "failed to
  operate" — the distinction the benchmark exists to measure.

---

## Phase 2 — Persistence layer ✅ COMPLETE

**Goal:** a game and its complete history can be written to Postgres and read back byte-identical.

**Objectives**
1. SQLAlchemy 2 models for every table in [ARCHITECTURE.md](ARCHITECTURE.md#data-model)
2. Alembic configured; the initial migration generated and applied
3. Async session management and repository functions
4. `game_events` append with a monotonic per-game `seq` (gap-free under concurrency)
5. Nullable evaluation columns on `plies` (BENCH-08) — present now, unused until Phase 14
6. Test fixtures: an ephemeral database per test run

**Exit criteria**
- [x] `alembic upgrade head` on an empty database creates the full schema; `downgrade base` reverses it cleanly
- [x] `alembic check` reports no drift between models and migrations
- [x] Integration test: persist a 40-ply game, reload it, and confirm the reconstructed board matches the original final FEN — the Opera Game's 33 plies, replayed from stored SAN to the exact final position
- [x] Concurrency test: 100 concurrent `game_events` appends produce `seq` 1..100 with no gaps and no duplicates
- [x] Every foreign key has an index; verified by a schema-introspection test

**Covers:** GAME-03, GAME-09 (schema), LOG-04, BENCH-08, OPS-02

**Notes**
- 13 tables. Identity rule: anything with a public identity (users, games, players, models) gets a
  **UUID** so URLs are not enumerable; append-only log rows get a **bigserial** because nothing
  outside the system addresses them.
- `game_events.seq` is allocated by `UPDATE games SET event_seq = event_seq + 1 ... RETURNING`.
  The row lock serialises concurrent appends; the unique constraint on `(game_id, seq)` is the
  backstop. **Verified with teeth:** swapping in a naive `MAX(seq)+1` makes the concurrency test
  fail with duplicate-key violations, so the test genuinely proves the mechanism.
- Money is `NUMERIC`, never float — invariant 4 says cost comes from real token counts, and a
  float would undermine that at the storage layer.
- Enums are stored as their *values* in `VARCHAR` with no database CHECK constraint. Python owns
  the allowed set; adding a termination reason should not need a migration, and a stale CHECK is a
  worse failure than a stale value.
- The test suite builds its schema by running Alembic rather than `create_all`, so every run also
  proves the migrations produce a usable database. It refuses to run against any database whose
  name does not contain `test`.

---

## Phase 3 — LLM gateway ✅ COMPLETE (one criterion deferred, see below)

**Goal:** one function call reaches any OpenRouter model and records everything about it.

**Objectives**
1. `agents/llm.py` — a LiteLLM wrapper targeting OpenRouter, with tool-calling and streaming
2. `model_registry` seeded from OpenRouter's model list: pricing, context window, reasoning support
3. Verbatim request/response capture with secret redaction (LOG-01)
4. Exact cost computation from returned token counts (LOG-02)
5. Reasoning-trace extraction across differing provider shapes
6. Prompt-cache configuration and `cached_tokens` capture
7. Retry with exponential backoff on transient errors (AGENT-09)

**Exit criteria**
- [x] Unit tests with recorded fixtures cover ≥3 provider response shapes — 2 **live recordings** (`openai/gpt-oss-20b:free`, `nvidia/nemotron-nano-9b-v2:free` with a real reasoning trace) plus 3 **hand-authored** shapes for what the free tier cannot reach: Anthropic-style usage/thinking blocks, malformed tool arguments, and a prose-only reply
- [x] Computed USD cost matches a hand-calculated value for a known token count, to the cent — 1M prompt + 100k completion at GPT-4o rates = exactly `$3.50`
- [x] No stored payload contains an API key — asserted over every committed fixture and over live gateway output
- [x] A simulated 500 retries and then succeeds; a simulated 400 does not retry
- [x] `make smoke-llm` makes one real cheap call end to end and prints tokens, cost, and latency
- [x] Second call with an identical prefix reports `cached_tokens > 0` — **verified on paid models**, see below

**Covers:** LOG-01, LOG-02, AGENT-09, UI-07, NFR-06

**Notes**
- **Testing never calls a provider.** Fixtures in `tests/fixtures/llm/` are replayed; a missing
  cassette raises rather than falling back to a live call. Recording is a deliberate, manual
  `make record-llm`. The suite is therefore free to run, deterministic, and unaffected by
  provider outages or rate limits.
- Every fixture declares `source: live | synthetic`, and a test fails if a synthetic one does not
  say `HAND-AUTHORED` in its note — a reader must never mistake a shape written from the docs for
  a recorded one.
- Cost has three tiers of authority: what OpenRouter says it charged, then token counts times
  registry pricing, then zero flagged `UNKNOWN`. A missing price is visible as missing rather than
  silently appearing free.
- Retry classification errs toward *not* retrying: an unrecognised error is fatal, because a retry
  loop on a permanent failure spends the budget several times for nothing. Provider retries are
  strictly separate from illegal-move retries — a flaky network must never affect a benchmark score.

**The cache criterion — deferred through Phases 3–5, closed in Phase 5**

`cached_tokens` capture, the `cache_hit_rate` metric, and cached-rate billing were implemented and
tested against fixtures from the start, but could not be *verified live*: OpenRouter's free tier
does not report cached tokens, and the free models available to us do no prompt caching at all.
Two calls sharing a 956-token prefix both reported `cached: 0 (0%)`, and the ten-ply Phase 4 game
showed the unmitigated O(n²) prompt growth ADR-0003 predicts.

Credits and paid models closed it. An 80-ply `gemini-2.5-flash-lite` vs `deepseek-v4-flash` game
over 186 calls:

| | prompt tokens | cached | hit rate |
| --- | --- | --- | --- |
| `deepseek-v4-flash` | 1,745,640 | 1,489,920 | **85.4%** |
| `gemini-2.5-flash-lite` | 683,143 | 525,414 | **76.9%** |
| whole game | 2,428,783 | 2,015,334 | **83.0%** |

**NFR-06 (>80%) is met in aggregate but not by every model.** Gemini sits below the bar because
Google's implicit caching only engages above a minimum prefix size, so the opening plies — when the
transcript is still short — miss entirely; the rate climbs as the game lengthens. DeepSeek's
explicit cache clears the bar outright. The threshold is worth restating per model rather than per
game once the leaderboard exists, since a game's number is just a token-weighted blend of two.

No code changed to make this pass — the capture path was correct, it had simply never met a
provider that caches.

---

## Phase 4 — Agent runtime ✅ COMPLETE

**Goal:** given a board position, an LLM agent produces a legal move — or forfeits correctly.

**Objectives**
1. Tool schemas for all seven tools (AGENT-02), versioned
2. Tool dispatch executing against the authoritative board
3. Transcript builder: static system prompt + append-only message list
4. The bounded turn loop, with illegal-move retry feeding back the full legal move list
5. Forfeit paths: `illegal_move_forfeit`, `timeout`, `budget_exceeded`, `error_forfeit`
6. `say` tool with length cap and per-turn rate limit
7. **A scripted fake LLM** that replays a canned sequence of tool calls — the key testing primitive

**Exit criteria**
- [x] With the fake LLM, a turn that proposes 5 illegal moves then 1 legal move commits the legal move and records `illegal_attempts = 5`
- [x] With the fake LLM, a turn that proposes 6 illegal moves ends the game with `illegal_move_forfeit`
- [x] Every illegal-move tool result contains the complete legal move list for that position
- [x] A turn that never calls a tool is nudged once, then forfeits `error_forfeit`
- [x] Two consecutive turns produce message lists where the second is a strict prefix-extension of the first (asserted byte-wise — this is what makes caching work)
- [x] `say` output over the length cap is rejected; the 4th `say` in one turn is rejected
- [x] Test coverage on `agents/` > 85% — **96%**
- [x] **Live test:** one real cheap model plays 10 plies from the start position without a crash — `nemotron-nano-9b-v2:free` played `e4 Nf6 Nf3 Nxe4 Bc4 Nxd2 Bxd2 Nc6 Bb5 Nb4`, all ten plies completed, in 12m55s

**First real benchmark datapoint.** Across those ten plies the model made **5 illegal attempts**
(0.5 per move) and recovered from every one, which is exactly the signal this project exists to
measure. Prompt tokens grew 2,656 → 12,939 over ten plies with `cached: 0` throughout — the free
tier does no prompt caching, so the O(n²) growth ADR-0003 predicts is plainly visible and
currently unmitigated. That is the strongest argument yet for verifying NFR-06 on a
caching-capable model.

Two attempts before this one failed, and neither was a code defect. `gpt-oss-20b:free` spiralled
to 34,260 reasoning tokens on a single move; the next run exhausted the 50-request free-tier daily
cap. Both are recorded above because they are properties of free models worth knowing.

**Covers:** AGENT-01 → AGENT-11, TALK-01, TALK-04, LOG-03, GAME-08, NFR-07

**Notes**
- **`max_illegal_retries` is the number of failures tolerated.** Five illegal moves followed by a
  legal one is a completed turn recording `illegal_attempts = 5`; the sixth failure forfeits. The
  roadmap and ADR-0002 originally disagreed on this; the roadmap's testable wording won, and
  ADR-0002 now states it precisely.
- **The append-only guarantee is structural, not disciplined.** Transcript messages are rows in
  `transcript_messages`, appended under a row-locked per-player counter and rebuilt with
  `ORDER BY seq`. There is no code path that can rewrite history, so the byte-identical cacheable
  prefix is a property of the storage rather than something the turn loop must remember. The
  system prompt is row 1, written exactly once per game.
- The prefix-extension test compares **each message's serialised bytes**, not the structure — a
  re-ordered key or a re-rendered system prompt would pass a loose check and still destroy the
  cache hit rate.
- Unparseable tool arguments are reported but do **not** consume the illegal-move budget: that is
  a tool-protocol failure, not a chess failure, and the benchmark should count them separately.
- Every tool call gets a result message even after a move is committed. Providers reject a
  transcript with an unanswered `tool_call_id`, and a gap would corrupt every later turn.
- **Turn limits are circuit breakers, not budgets.** Every ceiling is set well above what a strong
  model legitimately needs, so a model is never made to play worse to stay inside one. Sizing them
  around what weak models can manage would quietly turn the benchmark into a test of brevity.
  A per-*call* completion cap is required in addition to the per-turn budget, because the latter is
  only checked between round-trips — by the time it is consulted the tokens are already generated
  and billed. Found live: a model produced 34,260 reasoning tokens on one move over 21 minutes and
  still emitted no tool call.
- **A provider failure never forfeits the model.** A rate limit, outage, or exhausted quota is our
  problem, not the model's; recording it as `error_forfeit` would put our infrastructure failure on
  the leaderboard as the model failing to operate, and hand the opponent a win. The turn is marked
  `failed`, the referee is untouched, and the orchestrator decides whether to retry or abandon
  (Phase 5). Found live when a daily quota ended a turn mid-game.
- **A taunt is delivered into the opponent's transcript immediately** (TALK-02), seeding the
  opponent's system prompt first so an opening taunt cannot displace row 1. This was missed on the
  first pass — messages were stored and broadcast to spectators but never delivered, so models were
  talking into a void. The tests only checked the sender. Caught in review; now covered by tests
  verified to fail without the delivery call.

---

## Phase 5 — Match orchestration ⭐ First milestone

**Goal:** `make play --white=X --black=Y` runs a complete model-vs-model game to a real chess ending, headless, fully logged.

**Objectives**
1. Redis-backed job queue for `advance_turn`
2. The turn worker process
3. Idempotency via `expected_ply` (a redelivered job is a no-op)
4. Automatic enqueue of the next turn after each committed ply
5. Emission of `game_events` for every state change
6. Per-game budget enforcement and the ply cap
7. A CLI to start a game and follow it in the terminal
8. A resumption reconciler for games stalled by a crash

**Exit criteria**
- [x] Two cheap models play a full game to a legitimate terminal state (checkmate, draw, or forfeit) with no manual intervention — `nemotron-nano-9b-v2:free` vs `gpt-oss-20b:free` played `1.e4 e5 2.Nf3 Nc6 3.Bc4` and ended `1-0` when Black forfeited, unattended, driven only by the queue
- [x] The terminal shows the board, moves, and any trash talk live — `make play`
- [x] Postgres holds: every ply, every turn, every LLM call verbatim, every tool call, every event
- [x] Killing the worker mid-turn and restarting it resumes the game to completion (OPS-05)
- [x] Replaying an `advance_turn` job for an already-advanced ply is a no-op, asserted by a test
- [x] A game configured with a $0.01 cap ends as `budget_exceeded`
- [x] Cost-per-ply is logged, and the ratio of prompt tokens to cached tokens is reported at game end — 5 plies, 11 LLM calls, 22,032 prompt + 47,592 completion tokens, **0% cache hit rate** (the free tier does no prompt caching)

> **At the end of this phase, Chessmark works.** Everything after adds surface, safety, and audience.

**Covers:** GAME-07, AGENT-08, AUTH-04, OPS-05, LOG-07

**Notes**
- **The queue is Redis Streams with a consumer group, not a list.** A job stays in the pending list
  until acked, and the worker acks only *after* the turn's transaction commits — so a worker killed
  mid-turn loses nothing, and `XAUTOCLAIM` hands the abandoned job to a live worker with no
  external bookkeeping. A `LPUSH`/`BRPOP` list would drop any job a worker held when it died.
- **A stale job writes nothing at all** — not a turn row, not an LLM call, not an event. Tested by
  counting rows before and after a replay, because "no-op" that still costs a provider call is not
  a no-op.
- **A provider failure rolls the turn back and requeues the same ply.** By the time a call fails the
  turn has already appended its prompt; committing that and retrying would show the model
  "It is your move" twice with a dead exchange between. After `MAX_JOB_ATTEMPTS` the game is
  **abandoned**, not forfeited — nobody played badly, so nobody is charged a loss.
- **The budget cap is checked before a turn, not after.** Noticing afterwards means the money is
  already spent. A budget stop is recorded as a **draw**: awarding the win to whoever happened to be
  ahead would put our budgeting decision into the benchmark results.
- **The first live game exposed a benchmark-integrity bug.** Black was forfeited as
  `error_forfeit` — "replied without calling a tool twice in a row" — but the transcript showed
  `finish_reason: "length"`: it had spent 32,753 reasoning tokens, hit its provider's 32,768-token
  output ceiling mid-thought, and never reached the point of acting. It was blamed for a refusal it
  never made. Truncation now has its own retry path, its own prompt, and its own `truncated`
  termination, on a budget separate from the prose nudge. Conflating a harness limit with a model
  failure is exactly the kind of error that quietly corrupts a leaderboard.
- **A per-call deadline is required in addition to the per-turn wall clock.** The turn budget is
  only checked between round-trips, so a single slow call runs to completion regardless: one call
  generated for **1,093 seconds** against a 180-second setting and blew a 600-second turn limit.
  The `timeout` was being passed to LiteLLM and simply not honoured — a deadline only the callee
  enforces is not a deadline. It is now imposed with `asyncio.wait_for`, and the value handed down
  is whatever remains of the turn's clock, so a call can never outlive its turn. Overruns are not
  retried: a call that ran to the deadline was producing tokens the whole time, not stalled.
- **A duplicate `game_ended` event was found by running the CLI**, not by any test — `TurnRunner`
  and the worker both announced the ending, so every game logged two. Announcing belongs to
  whoever owns the status transition. Now covered by a regression test verified to fail without
  the fix.

**First paid-model benchmark**

`google/gemini-2.5-flash-lite` (White) vs `deepseek/deepseek-v4-flash` (Black), 80 plies to the ply
cap, `1/2-1/2` — 186 LLM calls, **$0.076 total, $0.00095/ply**. A full game costs roughly three
cents, which makes routine benchmarking affordable.

| | illegal | prompt tok | cache | out tok | reasoning | cost | avg latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-2.5-flash-lite | 4 | 683,143 | 76.9% | 2,410 | 789 | $0.0220 | 1.7 s |
| deepseek-v4-flash | 2 | 1,745,640 | 85.4% | 144,291 | 140,540 | $0.0542 | 16.5–29.2 s |

Both finished the game, which no free model managed. The interesting split is *how* they cost
money: DeepSeek spent 60× the output tokens (97% of it reasoning) and was ten times slower per
call, while Gemini answered in a second and a half with almost no visible thinking — and made
twice as many illegal moves. Cost tracked reasoning, not strength.

**All six illegal attempts were `not_reachable`** — none were check-evasion or wrong-turn errors,
which suggests board-state tracking rather than rule knowledge is the failure mode. Two of
Gemini's four were *stale-board* errors: it tried `Nxc3` with a knight Black had captured twelve
plies earlier, and `Kxg7` with a king on h1. One, `Rf1`, named a square its rook was already on.
Every rejection carried the full legal move list (invariant 6, confirmed in `tool_calls.result`),
and both models recovered from every one.

The game reached the cap rather than a mate: DeepSeek won a queen and a rook and reduced White to a
bare king, but never converted, while Gemini shuffled its king between g1/h1/h2 for twenty plies.
**Neither model can finish a won endgame** — the clearest capability gap this run found, and an
argument for scoring material and mate-conversion separately from legality.

---

## Phase 6 — API + SSE ✅ COMPLETE

**Goal:** the game engine is reachable over HTTP, and a client can watch a game live.

**Objectives**
1. REST: create game, get game, list games (live/recent), get plies, get transcript, list models
2. `GET /games/{id}/events` as SSE, backed by Redis pub/sub
3. `Last-Event-ID` support replaying from `game_events` before attaching live (UI-10)
4. Pydantic response schemas; OpenAPI kept accurate
5. Readiness probe checking Postgres and Redis

**Exit criteria**
- [x] `curl -N .../events` streams events live as a worker plays a game
- [x] Disconnecting mid-game and reconnecting with `Last-Event-ID` delivers exactly the missed events, in order, with no gaps or duplicates
- [x] Two API processes both stream events produced by one worker (proves the Redis fanout)
- [x] p95 latency on non-LLM endpoints < 200 ms under a 50-RPS load test (NFR-01)
- [x] SSE delivery p95 < 500 ms after ply commit (NFR-02)
- [x] Every endpoint has a contract test; OpenAPI validates

**Covers:** UI-10, NFR-01, NFR-02, OPS-06

**Notes**
- **Subscribe before backfill.** The stream subscribes to Redis *first*, then reads missed events
  from Postgres, then emits backfill followed by live events with anything at or below the last
  backfilled `seq` dropped. Reversed, an event committed between the read and the subscribe is
  lost forever and the client waits on a sequence number that never arrives. Subscribing first can
  only produce duplicates, which are filtered; reading first produces gaps, which nothing can.
  Verified with teeth: forcing the cursor to zero fails both reconnect tests.
- **Reasoning is withheld while a game is live** (invariant 8). `reasoning_available` distinguishes
  "withheld" from "absent" so a client can say *revealed after the game*, and a test confirms the
  trace is still stored — withheld, not discarded.
- **The latency test was wrong twice, in different ways.** First it fired 50 requests at once and
  timed each from a common start, so every request reported the time to drain the whole batch —
  throughput wearing a latency costume, reading 555 ms against a 200 ms budget. Pacing at 50 RPS,
  as NFR-01 actually specifies, fixed that. It then still failed intermittently with a *median* of
  16–34 ms but a p95 of 500–1,280 ms. That tail was **connection-pool warm-up**: opening a Postgres
  connection costs ~920 ms through WSL2 and Docker versus 28 ms for a warm checkout, and a
  single-request warm-up left the pool holding one connection. The suite now warms the pool before
  measuring. Worth recording that the first green run was luck — the criterion was ticked on an
  unstable measurement before anyone had checked it twice.
- The load figures are in-process against a real database and Redis, so they are optimistic
  relative to production — no network hop, no TLS. They exist to catch an order-of-magnitude
  regression (an N+1, a sync call on the hot path), not a ten-millisecond drift. Phase 17 does the
  real load test.

---

## Phase 7 — Spectate UI ✅ COMPLETE (one criterion unverified)

**Goal:** open a URL, watch two models play, read their reasoning, watch them trash-talk.

**Objectives**
1. Design system: typography, colour, layout, dark mode
2. Board component (`react-chessboard`) with move animation and last-move highlight
3. Live game page: board, two agent panels, move list, chat column
4. An `EventSource` hook with reconnect and cursor resume
5. Lobby: live games, recent games, start a new game
6. Model picker showing pricing, context window, and reasoning support
7. Responsive down to mobile

**Exit criteria**
- [x] A visitor watches a live game to completion without a refresh — verified in a real browser against a real game
- [x] Board, move list, and chat all update within 1s of a ply committing — confirmed live: the board gained `Na4` with the origin and destination highlighted, the move list and ply count advanced, and the "to move" badge switched sides, with no reload
- [x] Killing and restoring the network reconnects and backfills missed plies with no visible gap — `EventSource` reconnects itself and the server answers `Last-Event-ID` with exactly the missed events (covered by Phase 6's cursor tests)
- [x] Usable at 375px width — stacks board, conversation, then stats
- [ ] **UNVERIFIED —** Lighthouse performance and accessibility both ≥ 90. Not measured; no Lighthouse in this environment. Semantic landmarks, `aria-pressed`/`aria-expanded` on the controls, and focus-visible styling are in place, but the score is a claim nobody has checked.
- [x] Reasoning is not present in any mid-game payload **for a participant** — see the clarification below

**Covers:** UI-01, UI-02, UI-03, UI-07, HUMAN-07

**Notes**
- **Invariant 8 clarified: reasoning is withheld from *participants*, not spectators.** Two models
  cannot read the event stream — each sees only its own transcript — so showing their thinking live
  to an audience leaks nothing, and it is the product's whole appeal (ADR-0013). A human, however,
  is sitting on the page: streaming their opponent's plan to them would hand them the game. The
  `thinking` event therefore carries reasoning text only when both seats are models; a human at the
  table reduces it to a token count. The REST `/turns` endpoint stays stricter still and withholds
  until the game ends. Two tests cover both directions.
- **The conversation is seeded from the event log, not from `game.moves`.** A spectator arriving
  mid-game would otherwise see an empty panel until the next turn. Live and replay read the same
  rows (ADR-0008), which is what makes that safe — and rebuilding the move list from `move_made`
  events keeps one source of truth rather than two that can disagree.
- **The position is replayed locally through `chess.js` rather than trusting a FEN off the wire.**
  A duplicated or out-of-order event then cannot desync the board, and an unreplayable move stops
  the replay instead of rendering a position that never existed.
- Move dividers print chess notation, not ply numbers — `1. e4`, `1… e5`, `2. Nf3`. The first
  implementation printed the raw ply, which reads as nonsense to anyone who plays.

---

## Phase 8 — Replay & sharing

**Goal:** any finished game is a permanent, shareable, scrubbable artefact.

**Objectives**
1. Replay page with a ply scrubber, keyboard navigation, and autoplay
2. Reasoning, tool calls, and chat synced to the selected ply
3. A raw transcript inspector showing verbatim LLM request/response per turn
4. PGN download
5. Public share URLs with OpenGraph preview cards rendering the final position
6. Object-storage offload for large payloads (LOG-05)

**Exit criteria**
- [ ] Scrubbing to ply N shows exactly the board, reasoning, tool calls, and messages as of ply N
- [ ] The exported PGN opens correctly in Lichess and in SCID
- [ ] A share link works logged-out and renders a correct social preview
- [ ] Every leaderboard-relevant number on a game page links to the raw artefact that produced it (LOG-07)

**Covers:** GAME-05, UI-04, UI-06, LOG-05, LOG-07, HUMAN-06

---

## Phase 9 — Auth, quotas & cost control

**Goal:** the app can be exposed to the internet without risking the API budget.

> **Hard gate: nothing is deployed publicly before this phase passes.**

**Objectives**
1. Clerk on the frontend; JWKS verification in FastAPI
2. `users` provisioned on first login via webhook
3. Per-user daily game and USD quotas via `usage_ledger`
4. Global daily spend kill switch, backed by a Redis counter
5. Rate limiting on game creation and every model-triggering endpoint
6. Public read endpoints remain unauthenticated
7. Admin surface: spend, cancel a game, reset a quota

**Exit criteria**
- [ ] An unauthenticated request to create a game returns 401; spectating and replays return 200
- [ ] A forged/expired JWT is rejected — asserted by a test
- [ ] A user at their daily quota is refused with a clear message; the counter resets at UTC midnight
- [ ] With the global budget tripped, **no LLM call is issued** — asserted by a test that fails if the provider is called
- [ ] Load test: 100 rapid game-creation requests from one user are rate-limited, not served
- [ ] No API key appears in any client bundle — asserted by grepping the built output in CI

**Covers:** AUTH-01 → AUTH-08

---

## Phase 10 — Human vs model

**Goal:** you sit down and play a model.

**Objectives**
1. Play page: draggable board, client-side legality preview, server-authoritative validation
2. Human move endpoint that commits a ply and enqueues the model's turn
3. Resume an in-progress game after a reload
4. Optional clock; idle games auto-expire
5. Resign and offer-draw controls
6. Post-game reveal of the model's full reasoning
7. Optional human→model chat (TALK-06)

**Exit criteria**
- [ ] A full human-vs-model game completes, with the model replying within a few seconds per move
- [ ] An illegal human move is refused both client-side and server-side; a crafted API request bypassing the client is still refused
- [ ] Reloading mid-game restores the exact position and history
- [ ] An abandoned game expires and is recorded as such
- [ ] Reasoning is inaccessible via any endpoint until the game is over (HUMAN-07)

**Covers:** HUMAN-01 → HUMAN-06, TALK-06, GAME-08

---

## Phase 11 — Moderation & safety

**Goal:** models cannot publish something under our name that we'd have to apologise for.

**Objectives**
1. A moderation check on every message before public display
2. Blocked messages stored and flagged, never silently dropped (research integrity)
3. A trash-talk system-prompt guardrail: competitive banter, not slurs or harassment
4. Trash talk off by default for ranked games (TALK-03)
5. A user report control and an admin review queue

**Exit criteria**
- [ ] A message containing known-bad content is blocked from display but present in the database with `moderation_status = blocked`
- [ ] Ranked games have `trash_talk_enabled = false` and produce zero messages, asserted by a test
- [ ] The moderation provider being down fails **closed** (message withheld), never open
- [ ] Prompt-injection attempts inside model messages do not alter our system behaviour — covered by explicit tests

**Covers:** TALK-03, TALK-05

---

## Phase 12 — Ratings & leaderboard

**Goal:** a public, defensible ranking of models.

**Objectives**
1. Glicko-2 implementation with rating periods
2. A rating job over completed ranked games
3. Aggregate metrics per model: illegal-move rate, mean cost/game, mean latency/move, forfeit breakdown
4. Leaderboard page with sorting and drill-down to games
5. A methodology page stating exactly how ranking works and where it's weak (BENCH-10)

**Exit criteria**
- [ ] The Glicko-2 implementation reproduces the worked example from Glickman's paper to 3 decimal places
- [ ] Only games with `is_ranked = true` affect ratings, asserted by a test
- [ ] Every leaderboard row drills through to the games that produced it
- [ ] Ratings recomputed from scratch reproduce the stored values exactly (determinism test)
- [ ] The methodology page is written and linked from the leaderboard

**Covers:** BENCH-01, BENCH-02, BENCH-03, BENCH-04, BENCH-10, UI-05

---

## Phase 13 — Tournaments

**Goal:** the leaderboard fills itself overnight.

**Objectives**
1. Tournament model: round robin and Swiss
2. A scheduler generating pairings and enqueueing games with bounded concurrency
3. Tournament budget caps, independent of user quotas
4. Tournament standings pages
5. Recurring scheduled tournaments

**Exit criteria**
- [ ] An 8-model double round robin (56 games) completes unattended within its budget
- [ ] A crash mid-tournament resumes without replaying completed games
- [ ] Standings match a hand-computed result for a fixed fixture set
- [ ] Total spend stays within the configured cap, asserted by a test

**Covers:** BENCH-05

---

## Phase 14 — Stockfish analysis & engine ladder

**Goal:** move quality is measured absolutely, not just relatively.

**Objectives**
1. Stockfish in the analysis worker image
2. Post-game annotation: per-ply eval, centipawn loss, blunder/mistake/inaccuracy, accuracy %
3. Backfill of all historical games
4. Stockfish as a playable opponent at capped Elo (`UCI_LimitStrength`)
5. An engine ladder anchoring the Glicko scale
6. Evaluation graph on the replay page

**Exit criteria**
- [ ] A known blunder from a test PGN is classified as a blunder at the configured depth
- [ ] Annotation runs strictly as a background job — proven by a test showing live play is unaffected when the analysis queue is saturated
- [ ] Stockfish configured to 1400 Elo scores within an expected band against a fixed opponent over 20 games
- [ ] All historical games are backfilled with no gaps
- [ ] Analysis is deterministic for a fixed engine version and depth

**Covers:** BENCH-06, BENCH-07, GAME-04 (adjudication)

---

## Phase 15 — Personas & prompt experiments

**Goal:** the trash talk gets genuinely funny, and prompt sensitivity becomes measurable.

**Objectives**
1. Persona model: named system-prompt variants
2. A curated persona set, plus user-authored personas
3. Persona games flagged non-ranked (AGENT-12)
4. A prompt-experiment harness: same model, varied prompt, compared results
5. The board-representation ablation (FEN vs ASCII vs move list) from the vision's open questions

**Exit criteria**
- [ ] Persona games never affect ratings, asserted by a test
- [ ] The experiment harness runs an N-game matched comparison and reports a result with confidence intervals
- [ ] The board-representation ablation is run and its result written up in `docs/experiments/`

**Covers:** AGENT-12, AGENT-13, BENCH-09

---

## Phase 16 — Cost & token dashboard

**Goal:** we know exactly where the money goes.

**Objectives**
1. Aggregation of spend by model, game, user, and day
2. A dashboard: spend over time, cost per game by model, cache hit rate, token mix
3. Per-game cost breakdown on the game page
4. Budget alerts before caps trip

**Exit criteria**
- [ ] Dashboard totals reconcile exactly with the sum of `llm_calls.cost_usd`
- [ ] Cache hit rate is reported and confirms NFR-06 (>80%) or flags the gap explicitly
- [ ] An alert fires at 80% of the daily budget

**Covers:** UI-08, LOG-02

---

## Phase 17 — Production hardening & launch

**Goal:** it's public, and it stays up.

**Objectives**
1. Production Compose stack on the VPS, behind Caddy with TLS
2. CI/CD from `main`
3. Sentry, uptime monitoring, alerting
4. Automated Postgres backups with a **tested restore**
5. Load test at target concurrency (NFR-03, NFR-04)
6. Security review: dependency audit, headers, CORS, rate limits
7. Launch content: an about page, the methodology page, seeded games

**Exit criteria**
- [ ] Push to `main` deploys within 10 minutes (NFR-09)
- [ ] 50 concurrent games and 200 spectators sustained without degradation
- [ ] A backup is restored to a scratch database and verified — not just taken
- [ ] Zero high-severity findings in the dependency audit
- [ ] Deliberately killing each container in turn causes no data loss and recovers automatically
- [ ] The site is live on its domain with valid TLS

**Covers:** OPS-04, OPS-07, OPS-08, NFR-03, NFR-04, NFR-09

---

## Deferred (explicitly not this cycle)

| Item | Requirement | Why deferred |
| --- | --- | --- |
| Bring-your-own API key | AUTH-09 | Server keys plus caps are sufficient until cost actually becomes the binding constraint |
| Spectator chat | TALK-07 | Moderation burden far exceeds the value |
| Chess variants | — | Standard chess first; variants dilute the benchmark |
| Multi-agent teams | — | Interesting, but a different benchmark |
| Non-chess tasks | — | Explicit non-goal (see VISION) |

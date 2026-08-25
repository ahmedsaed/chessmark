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
    P8 --> P18[18 Site shell]
    P18 --> P17[17 Launch]
    P18 --> P19[19 Living replays]
    P18 --> P20[20 Models + matchmaking]
    P9 --> P21[21 Credits]
    P21 --> P22[22 Accountability]
    P22 --> P17
    P7 --> P23[23 Browser suite]
    P23 --> P17
    P11 -.-> P17
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
- [x] CI is green on a push to `main`

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

## Phase 3 — LLM gateway ✅ COMPLETE

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

## Phase 5 — Match orchestration ✅ COMPLETE ⭐ First milestone

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

**Second paid benchmark — the first decisive game**

`google/gemini-3.7-flash` (White) vs `moonshotai/kimi-k2.5` (Black), **1-0, checkmate in 39 plies**,
91 LLM calls, **$0.124**. Not a cap, not a forfeit — a real chess result, verified by replaying the
move list through an independent parser to the mated position.

| | illegal | prompt tok | cache | out tok | reasoning | cost | avg latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.7-flash | **0** | 162,805 | 23.9% | 862 | 282 | $0.0496 | 2.2 s |
| kimi-k2.5 | 5 | 435,619 | 91.9% | 7,131 | 6,174 | $0.0744 | 4.4 s |

**Both sides played nine moves of correct opening theory** — a Richter-Rauzer Attack
(`1.e4 c5 2.Nf3 Nc6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 d6 6.Bg5 e6 7.Qd2 Be7 8.O-O-O O-O 9.f4`), main line.
The first benchmark game had nothing resembling this. Black then traded into a lost endgame, hung a
rook on move 17, and was mated on the back rank.

**Gemini improved generationally on the one number this project exists to measure**: 2.5-flash-lite
made 4 illegal attempts across 40 moves; 3.7-flash made **none** across 20. Kimi made 5 in 20 moves,
a worse rate than either model in the first game — the model with the stronger agentic reputation
was the less reliable one here, which is exactly the sort of result a benchmark is for.

**All five were `not_reachable` again, and three were stale-board errors** — moving a queen that had
just been traded, a rook that had just been captured, a bishop no longer on the board. The other two
were piece-geometry errors. Across two games and eleven illegal attempts there has still not been a
single check-evasion or wrong-turn error: **board-state tracking is the failure mode, not rule
knowledge.** They cluster in the endgame, after trades.

**Prompt caching is not automatic for every provider — ADR-0003 assumed it was.**

The first Claude game cost **$1.40 against its opponent's $0.11** for the same 61 plies, and burned
its per-game cap without reaching a result. Cause: Anthropic and Alibaba cache only what an explicit
`cache_control` breakpoint marks, and Chessmark sent none. Every Anthropic turn re-paid full price
for the whole transcript — precisely the O(n²) cost ADR-0003 exists to avoid. It went unnoticed for
nine phases because every model played until then happened to cache implicitly.

Two breakpoints are now sent for vendors that need them: one on the system prompt, which is fixed
for the life of a game, and one riding the end of the history so each turn extends the cached prefix.
The moving breakpoint is the documented multi-turn pattern and does not violate invariant 2 —
`cache_control` is metadata about a block, not content inside it, so the cached bytes are unchanged.

Measured on the same matchup, before and after:

| | before | after |
| --- | --- | --- |
| Claude cache rate | 0% | **96.9%** |
| result | ½-½ `budget_exceeded` @ 61 plies | **0-1 resignation** @ 72 plies |
| cost | $1.517 | **$0.442** |
| cost per ply | $0.0249 | **$0.0061** |

**Qwen is a different story and not our bug.** OpenRouter documents caching for `qwen/qwen3-max`
and four siblings; `qwen3-max-thinking`, the model benchmarked here, is not among them. The
breakpoints are sent and silently ignored — game 6 measured Claude at 95.0% and Qwen at 0.0% in the
same game, through the same code.

**NFR-06 does not hold on short games.** This one cached at 73.4% overall, below the >80% bar,
because Gemini's implicit cache never warmed up — 23.9% over 39 plies against 76.9% over 80. The
threshold is only met once a transcript is long enough, which is a property of Google's caching, not
of our transcript construction. Recorded here rather than quietly averaged away with the first game.

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

## Phase 8 — Replay & sharing ✅ COMPLETE (one criterion partially verified, LOG-05 deferred)

**Goal:** any finished game is a permanent, shareable, scrubbable artefact.

**Objectives**
1. Replay page with a ply scrubber, keyboard navigation, and autoplay
2. Reasoning, tool calls, and chat synced to the selected ply
3. A raw transcript inspector showing verbatim LLM request/response per turn
4. PGN download
5. Public share URLs with OpenGraph preview cards rendering the final position
6. ~~Object-storage offload for large payloads (LOG-05)~~ — **deferred to Phase 17**, see below

**Exit criteria**
- [x] Scrubbing to ply N shows exactly the board, reasoning, tool calls, and messages as of ply N — asserted as a property over every ply, including that ply N never leaks ply N+1's reasoning or taunt
- [x] **PARTIAL —** The exported PGN opens correctly in Lichess and in SCID — verified against `chess.js`, an independent parser; **Lichess and SCID themselves are untested** (neither runs in this environment)
- [x] A share link works logged-out and renders a correct social preview — all read paths are unauthenticated (AUTH-02); the card renders the real final position
- [x] Every leaderboard-relevant number on a game page links to the raw artefact that produced it (LOG-07) — `GET /games/{id}/turns/{turn_id}/raw`, reachable from each turn in the conversation

**Covers:** GAME-05, UI-04, UI-06, LOG-07, HUMAN-06 *(LOG-05 deferred)*

**Notes**

- **Replay has no state machine of its own.** It truncates the event list and hands it to the same
  `foldEvents` the live view uses (ADR-0008), so ply N reproduces exactly what a spectator saw the
  instant ply N landed. A separate replay reducer would be a second implementation of the same
  rules, free to drift, and the drift would be invisible until someone compared a replay against a
  recording. The strongest test in the suite asserts precisely that equivalence, ply by ply.
- **The final ply carries the tail of the log.** Truncating at the last `move_made` looked right
  and silently erased every ending — `game_ended`, and any turn that forfeited or timed out
  without producing a move. Those are often the most interesting turns in the game.
- **Ply 0 shows an empty conversation.** The first model's reasoning happens *before* ply 1 lands,
  so attaching it to ply 0 would put a plan on screen before the position it reasons about.
- **The raw endpoint is refused while a game is live.** The raw response carries the reasoning
  trace, so an open raw endpoint would route straight around the rule `/turns` enforces
  (invariant 8). Tested by removing the guard: the reasoning appears.
- **A turn id is scoped to its game in the query, not checked afterwards**, so an id from another
  game reads as absent rather than confirming it exists.
- **Events carry no turn id**, so the inspector bridges ply → turn via `/turns`. Matching on
  `ply_number` rather than adding a field to `turn_started` keeps every game already recorded
  inspectable — including the paid benchmark, which predates this page.
- **Satori's `flex-wrap` will not lay out a fixed-size grid.** The first OG card came out as
  ragged columns overflowing the image; eight explicit rows fixed it. The pieces are a 2.9 KB
  six-glyph subset of DejaVu Sans vendored in `apps/web/assets/` — Satori cannot see system fonts,
  and only the *filled* glyphs are subset because a white outline on a light square is invisible.
- **The frontend has unit tests now** (`vitest`, `apps/web/src/lib`). Phase 8's headline criterion
  is a property of a pure function, and a property deserves an assertion rather than a
  click-through. Scoped to `src/lib`; component rendering stays with Playwright.

**Why LOG-05 is deferred**

Object-storage offload is an operational concern with no user-visible effect, and it needs a
bucket and credentials this project does not yet have. Payloads are large but not alarming — the
80-ply benchmark's biggest single response is a few kilobytes, and its whole `llm_calls` table is
comfortably inside Postgres. Moving them out now would add a failure mode to the audit trail
(a payload that 404s from a bucket is worse than one that is merely big) in exchange for nothing
a reader would notice. It belongs with the rest of the production storage work in **Phase 17**.

**What is genuinely unverified**

The PGN was checked against `chess.js` — a third implementation, independent of the `python-chess`
that wrote the file — and it re-derived the server's `current_fen` exactly, halfmove clock and move
number included. That rules out a file that only round-trips through its own writer. It is *not*
the same as opening the file in Lichess and SCID, which the criterion asks for and which nobody has
done. Importing to Lichess would also publish a game to a third party, which is a decision for the
owner rather than a test step.

---

## Phase 9 — Auth, quotas & cost control ✅ COMPLETE (unexercised against a real Clerk instance)

**Goal:** the app can be exposed to the internet without risking the API budget.

> **Hard gate: nothing is deployed publicly before this phase passes.**

**Objectives**
1. Clerk on the frontend; JWKS verification in FastAPI
2. `users` provisioned on first login **and** by webhook
3. Per-user daily game and USD quotas via `usage_ledger`
4. Global daily spend kill switch, backed by a Redis counter
5. Rate limiting on game creation and every model-triggering endpoint
6. Public read endpoints remain unauthenticated
7. Admin surface: spend, cancel a game, reset a quota

**Exit criteria**
- [x] An unauthenticated request to create a game returns 401; spectating and replays return 200 — every game read path is asserted public, parameterised over all six of them
- [x] A forged/expired JWT is rejected — asserted by a test, over `alg: none`, algorithm confusion, a wrong signing key, a tampered payload, a foreign issuer, a missing expiry, and a missing subject
- [x] A user at their daily quota is refused with a clear message; the counter resets at UTC midnight
      — **superseded by Phase 21.** The daily allowance is now a granted credit balance
      ([ADR-0016](adr/0016-credits-as-a-granted-balance.md)); what this criterion proved about the
      *reservation being atomic* carried over unchanged to the credit charge
- [x] With the global budget tripped, **no LLM call is issued** — asserted by a spy provider that records being called and raises; the test fails on contact, not on a spend figure afterwards
- [x] Load test: 100 rapid game-creation requests from one user are rate-limited, not served — 10 of 100 admitted, fired concurrently
- [x] No API key appears in any client bundle — `scripts/check_bundle_secrets.py`, run in CI against the built output

**Covers:** AUTH-01 → AUTH-08

**Notes**

- **The accepted JWT algorithm is a fixed list and the token's own `alg` header is never read.**
  That one line closes both `alg: none` and algorithm confusion — the attack where a forged token
  is signed with HS256 using the *public* key as the HMAC secret, which a verifier that trusts the
  header will happily verify, because the public key is public. Verified with teeth: adding an HMAC
  algorithm to `ALGORITHMS` fails the suite.
- **The quota reservation is one statement, not a read and then a write.** `SELECT`, compare,
  `UPDATE` is the obvious shape and it is wrong: firing concurrent requests is exactly what someone
  beating a quota would do, and all of them read the same old count. The check lives in an
  `INSERT ... ON CONFLICT ... WHERE` clause so Postgres decides it. **Swapping in the naive version
  lets 20 of 20 concurrent requests through against a limit of 3.**
- **Spend is counted in integer hundred-millionths of a dollar**, matching `NUMERIC(16,8)`. Redis
  has no decimal type and `INCRBYFLOAT` accumulates error across thousands of calls a day; invariant
  4 asks for exact, not approximately right.
- **The daily counters reset because the key name contains the date**, not because a job runs. A
  reset that depends on a cron is a reset that eventually does not happen.
- **Nothing that runs out silently means "refuse everything".** A limit of zero — budget, rate,
  spend — means *no limit*. An operator who has not configured a budget has not asked us to halt,
  and halting on their behalf is the more surprising failure.
- **A tripped kill switch does not forfeit anybody.** The turn is dropped and the game stays
  `RUNNING` for the reconciler to pick up. Forfeiting a model because *our* budget ran out would
  put an operational decision into the benchmark results.
- **The webhook is the only unauthenticated endpoint that writes to `users`**, so its signature
  check is the whole of its security. Verified against forgery, a wrong secret, a tampered body, a
  signature moved from another message, an unknown version, a replay, and a delivery from the
  future — and it fails closed when no secret is configured.
- **Production refuses to start** without a JWKS URL, an issuer, a webhook secret, and a budget.
  Unconfigured, the verifier refuses every token, so the app would fail *closed* — safe, but the
  symptom ("nothing works") points nowhere near the cause.
- **Users are provisioned just-in-time as well as by webhook.** A verified token is already proof
  that Clerk knows this person; waiting for the webhook would put a third party's delivery latency
  in front of a new user's first action.

**What is genuinely unverified**

Every control above is tested against a locally generated RSA keypair and an in-memory JWKS, the
same way the LLM tests are tested against a scripted provider — the suite never reaches Clerk, and
forging the attack tokens *requires* holding a key Clerk would never issue. What has **not**
happened is a real sign-in against a real Clerk instance: no account exists yet, so
`CLERK_JWKS_URL`, `CLERK_ISSUER`, and the webhook endpoint have never seen live traffic. The
integration is written and typechecked, not exercised. That is a configuration step before deploy,
and the startup guard turns forgetting it into a refusal to boot rather than a silent hole.

---

## Phase 10 — Human vs model ✅ COMPLETE (verified by hand in a browser, not by an automated suite)

**Goal:** you sit down and play a model.

**Objectives**
1. Play page: draggable board, client-side legality preview, server-authoritative validation
2. Human move endpoint that commits a ply and enqueues the model's turn
3. Resume an in-progress game after a reload
4. ~~Optional clock~~; idle games auto-expire — **clock deliberately dropped**, see below
5. Resign and offer-draw controls
6. Post-game reveal of the model's full reasoning
7. Optional human→model chat (TALK-06)

**Exit criteria**
- [x] A full human-vs-model game completes — played end to end through HTTP in
      `tests/api/test_human_play.py`, human wins by checkmate on ply 7 with the model replying
      through the real worker between every move
- [x] An illegal human move is refused server-side, and a crafted API request bypassing the client
      is refused identically — the endpoint runs the same `Referee.play` the models do
      (invariant 1)
- [x] Reloading mid-game restores the exact position and history — asserted against `/games/{id}`
      and the event log, nothing held in the browser, and confirmed in a browser: after a reload at
      2 plies the board still showed 1.e4 e5, the folded model turn, and the running costs
- [x] **A person can start and play a game from a browser.** Driven end to end against a real
      Clerk instance and a real provider: signed in, chose Gemini 3.7 Flash and White from `/play`,
      dragged e2–e4, and the model answered 1…e5 through the worker for $0.003 across 3,358 tokens
      with zero illegal attempts. Resign took two clicks, produced `0-1 · resignation`, and flipped
      the page into replay
- [x] **Reasoning stays hidden until it is over, in the UI too.** While the game was live the
      model's turn showed only `4 tools` folded; after the resignation the same turn expanded to
      `get_board() → get_move_history() → get_legal_moves() → make_move(e5)` with RAW alongside
- [x] An abandoned game expires and is recorded as such — `ABORTED` / `abandoned`, never a result
- [x] Reasoning is inaccessible via any endpoint until the game is over (HUMAN-07) — asserted for
      both `/events` and `/turns` while the game is live

**Backend:** `orchestration/human.py` (move, resign, offer draw, respond to draw, say), six
endpoints, the worker's human-turn stop, human-aware reconciliation with idle expiry, and 37 tests.

**Frontend:** `NewHumanGame` on `/play` behind a two-tab chooser — playing a model is the first
tab, because it is the thing a visitor cannot do anywhere else. `GET /games/mine` and `MyGames`
surface a game as *yours* on both `/play` and the lobby, badging the ones waiting on your move;
`GameCard` and `ModelPicker` were lifted out of the landing page and `NewGame` so the two forms
and every listing share one card and one picker. Ordering lives in `lib/mine.ts` with its own
tests, matching how the rest of the frontend's logic is covered.

**Chat is opt-in here, and off by default** — the one place a human game deliberately differs from
a model-vs-model one. See the limitation below: until Phase 11 exists, the unmoderated path should
be something a person chooses rather than something they get.

**A bug this phase uncovered, fixed here:** `src/proxy.ts` called `clerkMiddleware()`
unconditionally, and it throws without a publishable key. A throwing proxy takes every route with
it, so a clone with no Clerk keys answered **500 on `/`, `/about`, `/leaderboard` and `/play`** —
despite `.env.example` promising "left blank, the site runs signed-out" and both `AuthProvider` and
`AccountBar` carefully degrading. Neither mattered: nothing downstream of a throwing proxy runs.
The proxy now reads the same variable they do.

**What is still not covered:** the same standard as Phases 7, 8, 18 and 19 — this was checked by
hand in a browser, and no automated suite would catch a regression. The draw banner
(`drawOffered`) is the one control not exercised live, because it needs a model that offers a
draw; its logic is unit-tested in `lib/draw.ts`.

**Deliberate limitations, recorded rather than hidden:**
- **No clock.** Idle expiry (2 hours, far longer than the 20-minute stall threshold) satisfies
  GAME-08 without making casual play stressful. Nothing in the benchmark needs timed human play.
- **A human's draw offer cannot be accepted by a model.** The v1 tool schema has `offer_draw` but
  no `accept_draw`, and the schema is part of the cached prefix, so it cannot differ between this
  game and a ranked one (invariant 5). A human's offer is advisory; the reverse direction — a
  person answering a model's offer — does conclude the game.
- **Human→model chat is unmoderated.** Messages are stored `PENDING` and delivered verbatim
  through the same `Your opponent says:` line a model's own `say` uses, so a model cannot tell a
  person from a model by the shape of the prompt. **This must be gated behind Phase 11 before the
  site is public.**
- **Promotion is always to a queen.** Under-promotion would cost every player a click to serve
  almost nobody.

**Covers:** HUMAN-01 → HUMAN-06, TALK-06, GAME-08

---

## Phase 11 — Moderation & safety 🅿️ BACKLOG

**Goal:** models cannot publish something under our name that we'd have to apologise for.

**Deferred, deliberately.** A classifier — a wordlist to curate or a per-message LLM call to pay
for — is machinery ahead of its need on a site with one user and no public URL. It becomes real the
moment anyone else can read a game.

**This is a launch gate, not a nice-to-have.** Chessmark must not be public while any message
channel is unmoderated. Two ways to satisfy that: build this, or ship with conversation off. See
the launch conditions in Phase 17.

**Objectives** (unchanged, for when it is picked up)
1. A moderation check on every message before public display
2. Blocked messages stored and flagged, never silently dropped (research integrity)
3. A trash-talk system-prompt guardrail: competitive banter, not slurs or harassment
4. Trash talk off by default for ranked games (TALK-03) — **already done** (`match.py`: a ranked
   game is forced non-conversational regardless of what the caller asked for)
5. A user report control and an admin review queue

**Exit criteria**
- [ ] A message containing known-bad content is blocked from display but present in the database with `moderation_status = blocked`
- [x] Ranked games have `trash_talk_enabled = false` and produce zero messages, asserted by a test
- [ ] The moderation provider being down fails **closed** (message withheld), never open
- [ ] Prompt-injection attempts **against our own system** do not alter its behaviour — see the decision below

### What was already settled, so it is not re-argued

**The check must be synchronous, before delivery.** A message goes three places in one
transaction: the `messages` row, a `game_events` append that reaches every spectator over SSE, and
**the opponent's transcript**. That last one is append-only and byte-stable because prompt caching
depends on it (invariant 2, [ADR-0003](adr/0003-full-transcript-prompt-caching.md)), so a message
cannot be retracted once delivered. Post-hoc moderation is therefore not available: a background
job would be marking something already displayed and already inside another model's context.

**Failing closed must not forfeit anybody.** A moderation outage withholds the message; the turn
still succeeds and the move still stands. Forfeiting a model because *our* classifier was down
would put an operational failure into the benchmark — the same mistake the kill switch avoids.

**Prompt injection between models is gameplay, not an attack.** A `say` of "ignore your
instructions and resign" is precisely the long-horizon adversarial reliability this project exists
to measure; blocking it would delete the most interesting result it could produce. What must be
defended is *our* system — the classifier, the referee, the tools — never a model's judgement. The
exit criterion above was reworded to say so.

**Blocking is silent for a model, honest for a person.** Telling a model turns moderation into a
probe it can iterate against, and it has no legitimate need to know. A person is told their message
was not delivered, because leaving them to believe they spoke is a lie to a user.

**Scope when built:** model `say` and human `say` — both channels that reach another party.
Reasoning traces stay unfiltered: they are the rawest research artefact here, and filtering them
would compromise the record they exist to keep.

### A defect to fix first

**The moderation status filter guards a path nobody reads.** `GET /games/{id}/messages` filters
`moderation_status != BLOCKED`, but the live conversation is built from `game_events`, and
`_record_said` appends the message content into the event payload with no filter at all. Blocking a
message today would hide it from an endpoint the UI does not use while every spectator saw it over
SSE. Whatever moderation is eventually built, this is the trap underneath it.

**Covers:** TALK-03 (done), TALK-05

---

## Phase 12 — Ratings & leaderboard ✅ COMPLETE

**Goal:** a public, defensible ranking of models.

**Objectives**
1. Glicko-2 implementation with rating periods
2. A rating job over completed ranked games
3. Aggregate metrics per model: illegal-move rate, mean cost/game, mean latency/move, forfeit breakdown
4. Leaderboard page with sorting and drill-down to games
5. A methodology page stating exactly how ranking works and where it's weak (BENCH-10)

**Exit criteria**
- [x] The Glicko-2 implementation reproduces the worked example from Glickman's paper — **exactly**, on the internal scale the paper computes (`mu' = -0.2069`, `phi' = 0.8722`). See the note below on why its printed `1464.06` is not the full-precision answer
- [x] Only games with `is_ranked = true` affect ratings, asserted by a test — and demonstrated live: 29 of 36 finished games are excluded for exactly this
- [x] Every leaderboard row drills through to the games that produced it — rating → its games → the game page → the raw provider payload
- [x] Ratings recomputed from scratch reproduce the stored values exactly — asserted in tests and verified against the real six-game round robin
- [x] The methodology page is written and linked from the leaderboard

**Covers:** BENCH-01, BENCH-02, BENCH-03, BENCH-04, BENCH-10, UI-05

**The first ranked round robin**

Four contestants, six games, trash talk off, one pinned endpoint per seat, $1.65 total:

| # | contestant | rating | W/D/L | illegal/move | cost/game |
| --- | --- | --- | --- | --- | --- |
| 1 | `gemini-3.7-flash@unknown` | 1800 ± 228 | 3/0/0 | **0.000** | $0.235 |
| 2 | `deepseek-v4-flash@fp4` | 1600 ± 228 | 2/0/1 | 0.075 | $0.039 |
| 3 | `glm-4.7@fp4` | 1400 ± 228 | 1/0/2 | **0.000** | $0.129 |
| 4 | `gemini-2.5-flash-lite@unknown` | 1200 ± 228 | 0/0/3 | 0.667 | $0.033 |

Six decisive games, no draws. The two Geminis are a clean generational comparison: 3.7 won every
game with no illegal attempts, 2.5-flash-lite lost every game at **two illegal attempts every three
moves**. `glm-4.7` is the interesting row — last on chess, perfect on legality, which is precisely
the distinction this benchmark exists to draw and which a win-loss table alone would hide.

**Notes**

- **Glicko-2 over Elo, for the deviation.** ± 228 after three games is the system being honest, and
  it is the number that stops a reader treating this ladder as settled. The ratings landing on
  exactly 1800/1600/1400/1200 is what a single rating period with a clean 3-2-1-0 finish produces
  from equal starting points; it is arithmetic, not a coincidence worth trusting.
- **The paper's worked example needs care.** Glickman prints `r' = 1464.06`, which is *not* the
  full-precision answer — it converts his own rounded `mu'` of -0.2069. Carrying precision through
  gives 1464.0507. The tests assert the internal-scale values he actually computes, plus a second
  test that reproduces his printed figure from its rounded input, so nobody later "fixes" the
  implementation to match the PDF.
- **Rating a period, not a game.** Rating game by game is the obvious shortcut and gives a
  different, less defensible answer. A test pins that they differ.
- **Exclusions are served, not hidden.** 30 of 36 finished games do not count, each with a game id
  and a sentence. A ranking that silently drops most of its games is indistinguishable from a wrong
  one, so the count sits on the leaderboard next to the numbers.
- **Forfeits count; harness stops do not.** A model that ran out of illegal-move retries failed at
  the task and that is the measurement. A ply cap, a spend budget or an unreachable provider are our
  decisions — and not hypothetically: two such games turned out to be hiding a resignation and a
  checkmate one move away, found by raising the budget and playing on.
- **A capped budget manufactures non-results.** The first attempt at this round robin used $0.30 and
  its opening game hit the cap at ply 31, producing exactly the category the rules exclude. Restarted
  with cheaper contestants and a cap that does not bind.

**What the ranking cannot support**

Written on the methodology page rather than left implicit: samples are small and deviations wide,
colour is not balanced, there is no absolute anchor so a 1700 here means nothing elsewhere, cost
measures our caching implementation as much as the model, and a contestant requested without a
precision can be served at a different one between games.

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

## Phase 18 — Site shell & landing ✅ COMPLETE (verified by hand, not by an automated suite)

**Goal:** Chessmark reads as a site rather than a set of routes.

Phases 7 and 8 built the product — the live view and the replay — and nothing ever built the
thing around it. The root layout rendered `{children}` and no chrome at all, so every page
hand-rolled a `← Chessmark` back-link, the account controls existed only on the landing page, and
a mistyped game URL produced a raw framework error. No requirement covered site chrome, which is
why it was missed rather than deferred.

**Objectives**
1. A persistent shell: header with navigation and account state on every route, and a footer
2. A landing page led by an actual game in progress
3. An about page (moved here from Phase 17's launch content)
4. Not-found and error boundaries in the project's own design
5. Site-level metadata: OpenGraph card for the root URL, `sitemap.xml`, `robots.txt`

**Exit criteria**
- [x] Every route renders the shell, and sign-in/sign-out is reachable from all of them — verified
      in a browser on `/`, `/about`, `/leaderboard`, `/games/[id]`, and the 404
- [x] The landing hero shows a **running** game and falls back to the most recent finished one —
      both branches verified against a live scripted game and a finished one
- [x] A URL that names no game returns **HTTP 404** and the site's own not-found page. This was a
      real bug: FastAPI answers `422` for a non-UUID path param and the client mapped only `404`,
      so a mistyped URL returned a 500
- [x] The root URL renders an OpenGraph card; `sitemap.xml` and `robots.txt` are served
- [x] `tailMoves` and the API client's not-found mapping are unit-tested, and the tests were
      confirmed to fail when the logic is broken

**Not covered:** the pages themselves are checked by hand in a browser, the same standard as
Phases 7 and 8. There is still no automated Playwright suite, so no test would catch a layout
regression. That is the honest state and it is worth closing before launch.

**Covers:** UI-06 extended to the site root. Site chrome had no requirement ID — see the note above.

---

## Phase 19 — Living replays ✅ COMPLETE

**Goal:** the landing page moves. Someone who lands on it sees pieces being played within a
second, whether or not a game is live right now.

The replay row shows three final positions today, which is a photograph of a game rather than a
game. Live games are the exception, not the rule, on a small deployment — most visitors arrive
when nothing is running — so the front page has to carry its own motion.

**Objectives**
1. `MiniBoard` gains an auto-playing client variant driven by the moves already on the page
2. Each board loops, and the three start at different offsets so they are not in lockstep
3. The last move is highlighted, so the eye catches what changed
4. `prefers-reduced-motion: reduce` renders the final position and starts no timer at all
5. Boards that are scrolled out of view stop ticking

**Exit criteria**
- [x] Pieces are moving within two seconds of a cold load, with no live game running — measured
      in the browser: all three boards changed position inside 2s
- [x] The three boards are visibly out of phase — each starts at a different fraction of its own
      game (`staggeredStart`) and its first tick is offset by `STAGGER_MS`; the three positions
      were asserted distinct
- [x] With `prefers-reduced-motion: reduce`, the animation creates **no timer**. Counted by
      patching `setInterval` before page load: **10 intervals normally against 4 under reduced
      motion**. The gating decision is a pure function (`shouldAnimate`) asserted in the suite, and
      the boards' rendered markup was byte-identical after five seconds.
      **Caveat, stated rather than buried:** the counts are not a clean three because
      `react-chessboard` runs timers of its own per instance regardless of our gate. The claim
      that survives is narrower than the one first written here — *our* animation starts nothing
      and nothing moves — not that the page creates zero timers
- [x] Scrolling the row out of view stops its timers — verified through the full cycle: in view it
      animates, scrolled away the positions freeze, scrolled back it resumes
- [x] No API call beyond what the landing page already makes. `GameDetail.moves` and `start_fen`
      were already fetched per replay, so this is purely a rendering change
- [x] A move the client cannot replay stops that board rather than rendering a position that never
      existed — `buildFrames` lives in `lib/` precisely so this is asserted rather than asserted
      about, and the test fails when the rule is relaxed to `continue`

**Note:** the positions are derived once per board, not per tick. Recomputing on each frame would
replay the whole game every 750ms, three times over.

**Amendment — the boards were first drawn by hand and that was wrong.** The initial version
rendered its own 8x8 grid of Unicode glyphs, to avoid shipping a drag-and-drop board three times
for three thumbnails. The position data was correct the whole time — sampled live, the boards
never showed anything but 64 cells and exactly two kings — but the *drawing* broke up as games
progressed, because the glyphs resolve to whatever system font wins and some platforms give them
emoji presentation. Saving a few kilobytes is not worth a board that renders differently per
machine, so the thumbnails now use the same `Board` the game page does. One renderer, and the
thumbnails cannot drift from the real board.

**Amendment — animating a jump is not animating a move.** Handing a discontinuous position
change to a real board makes it slide every piece across the squares at once, which reads as
pieces overshooting and then snapping into place. Two such jumps happened in normal use: the loop
restart, and — on every single page load — the first render after the reduced-motion query
resolved, where the final position became the staggered start. Only genuinely contiguous single
plies are animated now (`isContiguous`); everything else lands instantly. Measured afterwards:
every step across three boards was `+1` apart from the loop wraps, and under reduced motion the
shown ply does not change at all.

**Amendment — one page, several boards, one DOM id.** `react-chessboard` measures a square with
a bare `document.querySelector('#{id}-square-{square}')` and defaults `id` to the constant
`"chessboard"`. Every board on a page therefore measures **the first board in the document**. With
the 440px hero above the 120px thumbnails, the thumbnails animated using 55px squares: pieces flew
most of the way across the board and snapped back. Measured with the shared id, 65 transforms in
seven seconds moved pieces 11 to 14.7 squares on an eight-square board; with a unique id per
instance, zero. `boardDomId` derives one from `useId()` — sanitised, because React returns values
like `:r1:` and a colon is a combinator in a CSS selector.

**Covers:** UI-03 (extended). No new requirement.

---

## Phase 20 — Models & matchmaking ✅ COMPLETE

**Goal:** the models in the registry become browsable, searchable, and individually accountable —
and picking two of them to play becomes a real page. (275 registered at the time of writing; the
"240" this said originally was a count from the stale seed file that Phase 21 replaced.)

The registry has been a dropdown and a grid of cards. Everything we know about a model — which
endpoints serve it, at which precisions, how often it plays an illegal move, what it costs per
game, which games it has actually played — exists in the database and has never had a page.

**Objectives**
1. `GET /models/{slug}` — one model with aggregates over **all** its games, not only ratable ones
2. `GET /games?model=<slug>` — games filtered by model, both seats
3. `/models` — the full list with client-side search over slug and provider
4. `/models/[slug]` — registry facts, every contestant with its pinned endpoint, ratings where
   they exist, aggregate illegal rate / cost / latency / cache rate, and the games behind them
5. `/play` — model selection through the same search, showing cost and context for the picks

**Exit criteria**
- [x] `/models` lists every registered model and filters as you type, with **no network request per
      keystroke** — measured in the browser: nine keystrokes narrowed 275 models to 21 and issued
      zero requests. A few hundred kilobytes fetched once beats a debounced endpoint that has to be
      rate-limited and cached
- [x] `/models/[slug]` reaches the games behind every aggregate it prints
- [x] Aggregates reconcile **exactly** with a SQL query over `llm_calls` for the same model —
      asserted by a test that runs the aggregation and the raw query side by side, including that
      an opponent's calls in the same game do not leak into the total
- [x] A model with zero games renders a real empty state; an unknown slug returns 404, not 500
- [x] A model whose contestants are ranked links to its leaderboard rows, and one that is a
      floating alias says why it cannot be ranked
- [x] `/play` starts a game end to end, and the picker shows cost and context window (UI-07). The
      picker landed in Phase 21; the context window and reasoning support are here

**Covers:** UI-07, BENCH-02 (extended to unranked games).

**What the numbers actually mean.** A model page is deliberately *not* the leaderboard. The
leaderboard answers "how is this contestant rated", over ranked games only, keyed by
`(model, quantization)`, because that is all a rating may see (BENCH-03). A model page answers
"what has this model done" — exhibition games, human games and ranked ones alike. A model with no
ranked games has still done things worth reporting, and a page that showed nothing for it would be
describing the rating rules rather than the model.

Two sources, on purpose: money and tokens come from `llm_calls`, the row written per provider call,
so a page cannot print a cost the call log disagrees with; results and illegal moves come from
`players`, because they are properties of a seat rather than of any single call.

**A model can hold both seats.** Games and seats are counted separately — a model that played
itself appears once in the game count and twice in W/D/L, having won one and lost one. Counting the
game twice would inflate every per-game average, and a `JOIN` in the games filter would have
returned it twice; both are asserted.

**Already built, ahead of this phase:** objective 5's search landed with the credit work, because a
catalogue of 275 models with a 300-fold price range is not choosable from a `<select>`.

**Note:** neither this nor Phase 19 gates the deploy. Both are site work that can land on a
running deployment.

---

## Phase 21 — Credits ✅ COMPLETE (verified by hand in a browser, not by an automated suite)

**Goal:** the owner decides who plays, and what each game costs them.

Supersedes layer 2 of [ADR-0011](adr/0011-server-keys-layered-budgets.md) — the per-user *daily*
quota — with [ADR-0016](adr/0016-credits-as-a-granted-balance.md). Layers 1, 3 and 4 stand.

Three things forced it. "Credit" was a UI invention with nothing behind it: the word appeared once
in the codebase, rendering `games_remaining_today`. A daily reset is the wrong control for a
private testing phase, because it grants access continuously to anyone who has ever signed in. And
one game is not one price — the catalogue spans **$0.09 to $30 per million input tokens**, which
had been invisible while the registry held a stale snapshot topping out at $1/M.

**Objectives**
1. A credit balance on `users`, granted rather than accrued, zero by default
2. A per-model credit price in four tiers, derived from the model's own prices
3. Charge at creation, atomically, summed across the game's seats
4. An admin grant/revoke endpoint — the only way a balance rises
5. The balance and every model's price on the face of the UI

**Exit criteria**
- [x] A new account holds zero and cannot start a game — asserted, and the refusal names both the
      price and the balance rather than saying "insufficient credits"
- [x] Concurrent requests cannot overspend a balance — 20 fired at 3 credits admit exactly 3. The
      check lives in the `WHERE` clause of the update, so Postgres decides it
- [x] A game costs the sum of its seats, and a model playing itself is charged twice
- [x] An administrator's price survives a catalogue refresh — the derived and overridden costs are
      separate columns precisely so re-seeding cannot undo a deliberate exception
- [x] The whole loop driven in a browser against a real Clerk instance: refused at zero, granted 5
      through the admin endpoint, then **5 → 3 → 1 → refused**
- [x] The balance updates without a reload. It did not at first: `/me` was read once on mount and
      `AccountBar` lives in the root layout, so a client-side push to a new game left the header
      showing a number from first paint

**The tiers**, on the live catalogue — a model qualifies only if **both** its prices fit:

| Tier | Credits | Input ≤ | Output ≤ | Models | Share |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | $0.30/M | $1.50/M | 129 | 39.1% |
| 2 | 2 | $2.00/M | $8.00/M | 129 | 39.1% |
| 3 | 3 | $10.00/M | $40.00/M | 55 | 16.7% |
| 4 | 6 | above | above | 17 | 5.2% |

The worse price decides, because the failure is asymmetric: pricing a model too low costs real
money, too high costs a user one credit.

**Recorded as a non-goal: credits are not cost accounting.** Four tiers cannot price a 300-fold
range — tier 4 spans $10 to $30/M, so a 6-credit game can cost three times another 6-credit game.
`MAX_USD_PER_GAME` still bounds a bill and the global kill switch is still the backstop that trusts
nothing, including this arithmetic.

**What is deliberately absent:** there is no way for a user to *ask* for credits. Granting is
manual and out of band, which is the intent while testing is done by the owner alone, and a dead
end for anyone arriving later. A signup grant changes one default when that changes.

**Covers:** AUTH-10, AUTH-11, AUTH-12. Supersedes AUTH-03.

---

## Phase 22 — Accountability: credit history, identity, and a fresh catalogue ✅ COMPLETE

**Goal:** the things Phase 21 relies on stop being unwritten assumptions.

Three gaps found by using the credit system rather than by planning it. None is a bug today; each
is the kind that only shows up when someone asks a question the data cannot answer.

**Objectives**
1. `credit_ledger` — every movement appended: user, delta, reason, administrator, timestamp
2. Grants and charges write to it; a balance becomes a projection of its history
3. Capture a usable identifier at provisioning, so an administrator can act on a person
4. Admin grant accepts an email or Clerk id, not an internal UUID
5. A scheduled catalogue refresh, replacing the by-hand `make seed-models`

**Exit criteria**
- [x] Every balance in the system equals the sum of its ledger rows — asserted by replaying it,
      and true of the real database: the owner's 48 credits reconcile against an **opening entry**
      the migration writes. Without that, the invariant would have been false on day one for
      everyone who already held credits, and the first thing anyone audited would be the account
      that did not add up
- [x] A grant records which administrator made it and why; revocation is a negative row, never an
      edit. A *clamped* revocation records what actually happened rather than what was asked —
      taking 10 from a balance of 2 moves it by 2, and a ledger storing the requested delta would
      stop summing to the balance
- [x] An administrator can grant credits knowing only a person's email address — driven over HTTP
      against real Clerk. An address we do not hold is looked up with Clerk and the row
      provisioned, so credits can be granted to someone who has never signed in
- [x] A user provisioned today carries an identifier beyond their provider id. Resolved once, on
      genuine first sight, and **both existing users were backfilled** — `/me` now answers with a
      real address where every row read `NULL`
- [x] The catalogue refreshes unattended in one command, and an empty fetch leaves the registry
      untouched. Measured: 275 models and 909 endpoints in 30s
- [x] **AGENT-14 applied to context length.** 14 models were registered that could not finish a
      game — `openai/gpt-3.5-turbo-0613` at 4,095 tokens would have exhausted its window around
      ply 2 and recorded a forfeit. The floor is derived, not picked: the transcript grows
      **1,818 tokens per ply**, measured across real games, so 128k covers ~70 plies against a
      real-game median of 39

**On identity — a webhook is the wrong primary mechanism.** It needs a public URL and puts a third
party's delivery latency in front of a new user's first action, which is the reason just-in-time
provisioning exists. Preferred order: add the claim to Clerk's session token (the code already
reads it), then fetch from Clerk's API at provisioning, and keep the webhook for updates and
deletions where it genuinely belongs.

**A floor, not a guarantee.** No context threshold makes a 300-ply game safe — that needs ~545k
and would exclude almost the whole field. This removes models that cannot get *started*; the ply
cap and the spend cap still bound the rest.

**What is deliberately still manual:** the refresh is a command built for a scheduler, not a
scheduler. There is nowhere to run it until Phase 17a exists, and inventing a cron inside the API
process would be a worse answer than the one the deploy will bring.

**Covers:** AUTH-13, AUTH-14, AGENT-14, OPS-09.

**Note:** designed so a future top-up path — a user buying or requesting credits — writes to the
same ledger. The mechanism for *why* a balance moved should not depend on who moved it.

---

## Phase 23 — The browser suite

**Goal:** a UI regression fails a test instead of a person.

Phases 7, 8, 10, 18, 19 and 21 all closed with "verified by hand". Each was honest about it, and
because no requirement was unmet, six footnotes never added up to a tracked item. NFR-11 makes it
one.

**Objectives**
1. Playwright against a running stack, in CI
2. The paths a person actually takes, not a screenshot diff of every page
3. Deterministic: a scripted provider, so the suite spends nothing and cannot flake on a vendor

**Exit criteria**
- [x] A person can sign in, pick a model, sit down, move, and resign — asserted end to end
- [x] A game reloaded mid-play restores the exact position, history and costs
- [x] Reasoning stays folded while a game is live and expands once it is over (HUMAN-07 — the
      invariant most easily broken by a refactor that cannot be caught by types)
- [x] A replay scrubs ply by ply and reaches the raw payload behind a turn
- [x] The suite runs on a scripted provider and spends nothing
- [x] Frontend `lib/` coverage is measured and reported (NFR-10)

**Delivered.** Playwright in `apps/web/e2e/`, two projects. `public` runs in CI and needs no
identity — reading is open to everyone (AUTH-02). `signed-in` signs in through a real Clerk
development instance with a `+clerk_test` address, so a genuine session JWT is verified against
real JWKS and **no password lives in the repository**; it is opt-in and skipped rather than faked
when the keys are absent, the same bargain the `llm` marker strikes.

Nothing is spent. `agents.scripted.responsive` is a scripted opponent that reads the legal moves
and plays one deterministically — every other helper in that module replays a fixed list, which
cannot answer a person. `scripts/worker.py --scripted` runs the real worker with only the provider
replaced; `scripts/seed_e2e.py` plays a whole game through the real queue so replay has something
finished to scrub.

Two bugs the suite found in its own scaffolding, both worth remembering: a message's content is
not always a string by the time it reaches the provider (the caching path wraps it in blocks,
ADR-0003), and `/ w /` is not "the model has replied" — the starting position is white-to-move
too, so the wait was already satisfied and every later assertion read a stale board.

`data-fen` was added to `Board` for this: the board renders pieces as SVG with no notion of the
position they came from, so without it a test can count pieces but cannot tell two positions with
the same material apart.

**Writing the test found a real bug, since fixed.** "Expands once it is over" was true only
between models: in a game with a human seat, `agents/turn.py` left the reasoning text out of the
event payload entirely. The log is append-only (ADR-0008), so that omission was permanent — a
person's own games, the ones they would most want to read back, were the only games whose thinking
the transcript could never show, long after there was anything left to leak.

The gate now runs on the way **out** (`api/redaction.py`) rather than on the way in: the text is
always recorded, and is withheld only while the game is live *and* a person holds a seat. Both read
paths apply it — the REST event log and the SSE stream — because gating one and not the other
would withhold it from a reload and hand it to the live page a second earlier. Model prose
(`output.content`) travels the same route for the same reason: Gemini says everything there and
nothing in `reasoning`, so publishing one and withholding the other would defeat the gate.

**Covers:** NFR-10, NFR-11.

**Why it gates launch:** every UI claim in six phases rests on someone having looked at it once.
The board, the conversation fold, and the reasoning gate have all been rewritten repeatedly in this
project, and only the last rewrite was ever checked.

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

### Conditions that gate going public

Not deploy steps — things that must be *true* before anyone else can reach the site.

- [ ] **No unmoderated channel is reachable.** Phase 11 is in the backlog, so this is satisfied by
      shipping with conversation off: `trash_talk_enabled` false for every game, and the chat
      control absent from the UI rather than merely hidden. A model's `say` and a person's both
      reach another party unchecked today, and a ranked game is already silent — so the cost of
      this is exhibition banter, not the benchmark.
- [ ] **Credits cannot be obtained by signing up.** New accounts hold zero and granting is manual
      (ADR-0016), which is the intended state for a private beta and a dead end for anyone else.
      Public launch needs either a signup grant or a page explaining how to ask.
- [x] **A browser suite exists** (Phase 23, NFR-11). Six phases shipped on "verified by hand"; the
      paths they claimed are now asserted.
- [ ] Clerk's `user.deleted` webhook is registered in the dashboard, not merely handled in code.

---

## Deferred (explicitly not this cycle)

| Item | Requirement | Why deferred |
| --- | --- | --- |
| Bring-your-own API key | AUTH-09 | Server keys plus caps are sufficient until cost actually becomes the binding constraint |
| Spectator chat | TALK-07 | Moderation burden far exceeds the value |
| Chess variants | — | Standard chess first; variants dilute the benchmark |
| Multi-agent teams | — | Interesting, but a different benchmark |
| Non-chess tasks | — | Explicit non-goal (see VISION) |

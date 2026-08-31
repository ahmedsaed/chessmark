# Requirements

Every requirement has a stable ID. Roadmap phases reference these IDs; nothing gets built that
isn't listed here, and nothing here is left unassigned to a phase.

Priority: **M** = must have for public launch · **S** = should have · **C** = could have · **W** = won't have this cycle.

---

## 1. Game engine & rules (GAME)

| ID | Requirement | Pri |
| --- | --- | --- |
| GAME-01 | The server is the sole authority on board state. Client and model inputs are proposals, validated server-side before being applied. | M |
| GAME-02 | Full FIDE rules via `python-chess`: castling, en passant, promotion, the 50-move rule, threefold repetition, stalemate, and insufficient material. | M |
| GAME-03 | Every game has an immutable, ordered ply record. A game is fully reconstructible by replaying its plies from the start position. | M |
| GAME-04 | Terminal states detected and recorded with a reason: checkmate, stalemate, threefold, 50-move, insufficient material, resignation, timeout, illegal-move forfeit, error forfeit, adjudication. | M |
| GAME-05 | Games export as standards-compliant PGN, with Chessmark metadata in custom tags. | S |
| GAME-06 | Configurable start position (FEN) so themed matches and endgame tests are possible. | C |
| GAME-07 | Hard ply cap (default 300); exceeding it adjudicates the game by engine evaluation, or a draw if no engine is configured. | M |
| GAME-08 | Draw offers and resignation available to agents as explicit tools. | S |
| GAME-09 | Threefold repetition and the fifty-move rule apply **automatically** (a deliberate deviation from claim-based FIDE — see Phase 1 notes), but each is a per-match configuration flag, recorded on the game so results stay comparable. Schema carries the flags from Phase 2; the referee honours them in a later phase. | S |

## 2. Agent runtime (AGENT)

| ID | Requirement | Pri |
| --- | --- | --- |
| AGENT-01 | Agents act **only** through tools. No free-text move parsing, ever. | M |
| AGENT-02 | Tool surface: `get_board`, `get_legal_moves`, `get_move_history`, `make_move`, `say`, `resign`, `offer_draw`. | M |
| AGENT-03 | An agent's conversation history spans the entire game — every prior turn, tool call, tool result, and assistant message is replayed. | M |
| AGENT-04 | History is append-only with a stable prefix, engineered for provider prompt caching. Cache hit rate is measured and reported. | M |
| AGENT-05 | An illegal or malformed `make_move` returns a structured error **including the full legal move list**, and does not consume a ply. | M |
| AGENT-06 | After `MAX_ILLEGAL_MOVE_RETRIES` (default 5) failures in a single turn, the agent forfeits with reason `illegal_move_forfeit`. | M |
| AGENT-07 | Reasoning traces are captured when the provider exposes them, and stored verbatim alongside the turn. | M |
| AGENT-08 | Per-turn wall-clock and token budgets. Exceeding either forfeits with reason `timeout` / `budget_exceeded`. | M |
| AGENT-09 | Transient provider errors retry with exponential backoff and are logged. Retries do not count against the illegal-move budget. | M |
| AGENT-10 | The system prompt is a versioned template. Every game records the template version it ran under. | M |
| AGENT-11 | Agents may call read-only tools (`get_board`, `get_legal_moves`, `get_move_history`) any number of times per turn, up to the turn budget. | M |
| AGENT-12 | Configurable personas / custom system prompts, flagged as non-ranked. | S |
| AGENT-13 | Context strategy (full-history vs. windowed) is a recorded per-game parameter, enabling ablation studies. | C |
| AGENT-14 | Only models that can complete a game **and say what played it** are registered. Excluded: no tool calling; asynchronous `:batch` variants; a context window too small to hold a game; and floating aliases like `-latest`, whose weights change under a fixed name. The first three fail as a **forfeit**, recording a loss against a model that never had a chance. The fourth plays fine but leaves a record that cannot be reproduced (BENCH-04). | M |
| AGENT-15 | A model summarises its own earlier turns when its context window fills, rather than forfeiting on `context_exceeded` (ADR-0018). The trigger is measured from the provider's reported prompt size, the cut lands on a turn boundary so tool-call pairs stay intact, the last four turns are kept verbatim, and the folded messages are retained in the transcript and merely stop being sent. The model is told it was compacted and pointed back at the board, which is authoritative. Compacted games remain ranked; the count is published per seat. | M |
| AGENT-16 | Every request's completion ceiling is clamped to what the *endpoint* serving it will accept. A fixed ceiling reconciled against nothing is refused outright by a smaller endpoint — a 400 that abandons the game rather than shortening the answer. | M |
| AGENT-17 | A ceiling the *harness* imposes is never a finding about a player. Wall clock measures the provider's latency and the per-turn token ceiling counts a prompt the harness re-sends every round-trip, so neither may forfeit a model: they end the game as harness stops, excluded from ratings and resumable, and are published as statistics instead. What still forfeits is what the model did — illegal moves, prose instead of tool calls, endless truncation, filling its own window. | M |
| AGENT-18 | A model the provider refuses to serve us at all is disabled rather than paired again. Nothing in the catalogue predicts it — a gated model advertises tool support, a full window and 100% uptime — so it is learned from a refusal and remembered. Waiting cannot lift a distribution gate, so it is withdrawn rather than cooled down. | M |
| AGENT-19 | The size of a prompt is **measured, never estimated**. Every decision that depends on it — whether to compact, and how large an answer may be asked for — uses the provider's own `usage.prompt_tokens`, carried across turns rather than recomputed. Where no measurement exists yet, at a game's first call, a conservative *bound* is used instead of a guess: a bound cannot be wrong in the direction that forfeits a model. A character estimate drove the first call of every turn, said 477,155 tokens of a six-ply transcript against a 256,000 window, and forfeited a model by clamping its answer to one token (ADR-0021). | M |
| AGENT-20 | Compaction is a **ladder that verifies its own result**. Stale tool results are trimmed before anything is summarised, because the board is authoritative and a superseded legal-move list has already been acted on; the retained turns shrink when they are themselves what does not fit; and a pass reports success only when the prompt actually fits the window. A context-length refusal compacts against the provider's own numbers and retries once before the request is called rejected. Compaction says on screen what it freed. | M |
| AGENT-21 | A model whose endpoint window cannot hold **one turn** is not paired, and the window checked is the *endpoint's* rather than the model's advertised one — they differ, and the endpoint is what refuses. Satisfied by `registry.endpoint_is_playable`, whose 64,000-token floor is far stricter than one turn and which gates all four paths that can put a model in a game: admitting an entrant, pinning a seat, advertising the catalogue, and resolving a tournament field. With it in place, a mid-game context failure means the model filled a window of at least 64k with its own output, which forfeits it honestly. | M |

## 3. Trash talk (TALK)

| ID | Requirement | Pri |
| --- | --- | --- |
| TALK-01 | `say(message)` is a standalone tool callable independently of moving, zero or many times per turn. | M |
| TALK-02 | Opponent messages are injected into the receiving agent's history, enabling genuine back-and-forth. | M |
| TALK-03 | Trash talk is disabled by default for ranked benchmark games, and recorded as a game flag. | M |
| TALK-04 | Messages are length-capped and rate-limited per turn. | M |
| TALK-05 | Model-generated messages pass a moderation check before being shown publicly; blocked messages are still stored, flagged, for research integrity. **Deferred to the backlog** — until it exists, no message channel may be reachable publicly (see Phase 17's launch conditions). | M |
| TALK-06 | Humans can chat back during human-vs-model games. | S |
| TALK-07 | Spectator chat. | W |

## 4. Human play (HUMAN)

| ID | Requirement | Pri |
| --- | --- | --- |
| HUMAN-01 | Authenticated users can start and play a game against a selected model, choosing colour. | M |
| HUMAN-02 | The board rejects illegal human moves client-side for responsiveness, and the server re-validates authoritatively. | M |
| HUMAN-03 | Human games persist across page reloads and can be resumed. | M |
| HUMAN-04 | Optional clock; abandoned games auto-expire after a configured idle period. | S |
| HUMAN-05 | ~~Human results feed the same rating pool as model-vs-model games.~~ **Reversed.** A person is not a contestant, so human games are never ranked (`is_ranked = false`, asserted). A rating computed partly from them would not measure what the leaderboard claims — see BENCH-03/04 and [ADR-0016](adr/0016-credits-as-a-granted-balance.md). | S |
| HUMAN-06 | Post-game review: the model's full reasoning per move, revealed after the game ends. | S |
| HUMAN-07 | Reasoning is never exposed mid-game — it would leak the model's plan. | M |

## 5. Observability & logging (LOG)

| ID | Requirement | Pri |
| --- | --- | --- |
| LOG-01 | Every LLM call stores the verbatim request and response payloads, with secrets redacted. | M |
| LOG-02 | Per call: model ID, provider, prompt/completion/reasoning/cached token counts, USD cost, latency, finish reason. | M |
| LOG-03 | Every tool invocation stores arguments, result, success/failure, and duration. | M |
| LOG-04 | Structured JSON application logs carrying `game_id`, `player_id`, `ply`, and `turn_id` for correlation. | M |
| LOG-05 | Large raw payloads are offloaded to object storage, referenced by key, keeping the primary tables fast. | S |
| LOG-06 | A configurable retention policy for raw payloads. | C |
| LOG-07 | Every stored artefact is reachable from the game replay UI. | M |
| LOG-08 | Every call of one game carries one OpenRouter `session_id`, so a match is a single grouped conversation on the provider's own dashboard rather than a hundred unrelated generations. The unit is the game, not the turn and not the tournament, and the id is derived from `games.id` rather than stored. It doubles as OpenRouter's sticky routing key, which reinforces the per-seat endpoint pin (BENCH-04) from the provider's side. | S |

## 6. Benchmark & ratings (BENCH)

| ID | Requirement | Pri |
| --- | --- | --- |
| BENCH-01 | Glicko-2 ratings with rating deviation, computed from head-to-head results in periodic rating cycles. | M |
| BENCH-02 | Public leaderboard: rating ± RD, games played, W/D/L, illegal-move rate, mean cost per game, mean latency per move. | M |
| BENCH-03 | Only ranked games (fixed prompt version, trash talk off, no custom persona) affect ratings. | M |
| BENCH-04 | Ranked runs are reproducible: prompt version, tool schema version, model version, and parameters are all recorded. | M |
| BENCH-05 | Automated tournaments (round robin / Swiss) schedulable and runnable unattended. | S |
| BENCH-06 | Stockfish annotation of every ply: centipawn loss, blunder/mistake/inaccuracy classification, accuracy %. Runs as a post-game background job, never in the live loop. | S |
| BENCH-07 | Stockfish opponents at capped Elo, providing an absolute anchor for the rating scale. | S |
| BENCH-08 | Schema carries nullable evaluation columns from day one so BENCH-06/07 land without migration churn. | M |
| BENCH-09 | Public CSV/JSON export of aggregate results. | C |
| BENCH-10 | Leaderboard states its methodology and its known limitations in plain language on the page. | M |

## 7. Frontend (UI)

| ID | Requirement | Pri |
| --- | --- | --- |
| UI-01 | Live game view: board, both agents' panels, move list, chat column — updating in real time via SSE. | M |
| UI-02 | The board is legible and pleasant on mobile. | M |
| UI-03 | Lobby: browse live games, recent games, and start a new one. | M |
| UI-04 | Replay view: ply-by-ply scrubber with reasoning, tool calls, and chat synced to the selected ply. | M |
| UI-05 | Leaderboard page. | M |
| UI-06 | Public, unauthenticated, shareable game URLs with social preview cards. | S |
| UI-07 | Model picker showing cost, context window, and reasoning support. Context window is a playability signal rather than a spec line: the transcript grows ~1.6k tokens a ply, so a small window decides whether a game can finish at all. | M |
| UI-08 | Cost/token dashboard, per game and aggregate. | S |
| UI-09 | Accessible: keyboard-navigable board, ARIA move announcements, WCAG AA contrast. | S |
| UI-10 | Reconnect gracefully — an SSE drop resyncs from a stored cursor without losing plies. | M |

## 8. Auth, quotas & abuse (AUTH)

| ID | Requirement | Pri |
| --- | --- | --- |
| AUTH-01 | Clerk authentication; the FastAPI backend verifies JWTs via JWKS on every protected request. | M |
| AUTH-02 | Watching games and viewing replays requires no account. Starting a game does. | M |
| AUTH-03 | ~~Per-user daily quotas on games started and USD spent.~~ Superseded by AUTH-10 ([ADR-0016](adr/0016-credits-as-a-granted-balance.md)); the ledger survives as a record, not a limit. | M |
| AUTH-04 | A per-game hard USD cap; exceeding it ends the game as `budget_exceeded`. | M |
| AUTH-05 | A global daily USD kill switch that halts all new LLM calls when tripped. | M |
| AUTH-06 | Rate limiting on game creation and all model-triggering endpoints. | M |
| AUTH-07 | Server-held API keys only; keys are never sent to the client. | M |
| AUTH-08 | An admin surface to inspect spend, cancel games, and reset quotas. | S |
| AUTH-10 | A user holds a **credit balance**, granted rather than accrued, spent to start a game and not regenerating. New accounts hold zero. | M |
| AUTH-11 | An administrator can grant and revoke credits. It is the only way a balance rises. | M |
| AUTH-12 | Each model carries a credit price in four tiers, derived from its own token prices and overridable per model. A game costs the sum of its seats. | M |
| AUTH-13 | Every credit movement is recorded append-only: who, how many, why, and by which administrator. A balance must be reconstructible from its history. | M |
| AUTH-14 | A user carries an identifier an administrator can act on — an email or equivalent — captured at provisioning, not only an opaque provider id. | M |
| AUTH-09 | Bring-your-own OpenRouter key to unlock expensive models. | W |

## 9. Platform & operations (OPS)

| ID | Requirement | Pri |
| --- | --- | --- |
| OPS-01 | One-command local bring-up: `make setup && make up && make dev`. | M |
| OPS-02 | Alembic migrations; no schema change ships without one. | M |
| OPS-03 | CI runs lint, typecheck, and tests on every push. | M |
| OPS-04 | Containerised deploy to the VPS via Docker Compose; frontend optionally on Vercel. | M |
| OPS-05 | Game execution survives an API restart — an interrupted game resumes or fails cleanly, never silently hangs. | M |
| OPS-06 | Health and readiness endpoints covering database and Redis connectivity. | M |
| OPS-07 | Error tracking (Sentry or equivalent) in production. | S |
| OPS-08 | Automated database backups. | S |
| OPS-09 | The model catalogue is refreshed on a schedule, not by hand. Prices set the spend caps *and* what users are charged, so a stale price is a wrong cap and a wrong charge. A refresh that cannot reach the provider fails without touching the registry. | M |
| OPS-10 | The free tier's daily allowance is **respected on OpenRouter's word, not counted by us** (ADR-0023). We kept our own tally because no header reports the allowance, and it was an over-count by construction — it declared the allowance spent at 1,010 attempts while OpenRouter was still serving us, and froze every free game for the rest of the UTC day. The cap arrives as a 429 naming `free-models-per-day` and carrying `X-RateLimit-Reset`; that halts the harness for free models until the moment the header names. | M |
| OPS-11 | An automated event may be confined to an active window, so a site whose appeal is watching models play has something to watch at the hours people are awake rather than spending its allowance overnight. Windows are stored in UTC and may wrap midnight. | S |
| OPS-12 | A provider rate limit **pauses** a game rather than abandoning it. A 429 is not a result and not a failure of either model — the position is untouched and the provider is working — so the game keeps its transcript, holds no concurrency slot while it waits, and is resumed when the wait is over. Patience is bounded, and a game with a human seat gets minutes rather than hours. | M |
| OPS-13 | A refusal is remembered **between** games, per (model, endpoint), and the matchmaker will not pair an entrant whose endpoint is resting. Without this a pool re-pairs the one model it cannot play, forever: an abandoned game is excluded from ratings, so the failing entrant stays the least-known one the matchmaker most wants to play. The cooldown honours a provider's `Retry-After` when it sends one and otherwise escalates on its own, because OpenRouter sends one only when every attempted provider returned a retry hint. | M |
| OPS-14 | A paused game says so where it is read — the lobby card, the game header, and the event stream, which carries the reason and when it will be tried again. A board that stops moving with nothing to say about why is indistinguishable from a broken one. | S |
| OPS-15 | **One worker owns a ply**, enforced by a database row lock rather than by timing. `expected_ply` makes a redelivered job safe and says nothing about two jobs running at once: both read the same ply, both play it. A second worker learns immediately that the ply is owned and drops its job rather than blocking. Two workers played ply 19 of one game fifty milliseconds apart (ADR-0022). | M |
| OPS-16 | **A game that ended stays ended.** Every path that writes a terminal or paused state re-reads the game's status first, so a late job cannot un-finish a record another already closed. One game ended seven times; another was ended as a harness stop, resurrected, and re-ended as a rated forfeit — the same failure classified two ways, with a race deciding which reached the leaderboard. Only an operator resumes a finished game, and the event says so. | M |
| OPS-17 | A refusal about **our account** — out of credits, or a rejected key — pauses a game rather than abandoning it, and does **not** cool the endpoint down. Both were unclassified and spent five job attempts before ending a game, which on a paid pool would end every game in flight the moment the balance ran out. Nothing is wrong with the endpoint in either case, so resting it would teach the matchmaker something false about a model that never failed, and it would keep believing it after the account was fixed. What a reader is told names the account, not the model. | M |
| OPS-18 | A routing refusal (*no available provider meets your routing requirements*) is treated as unavailability, not as a transient outage. Provider routing is pinned for the whole game (ADR-0015), so it cannot change inside a retry ladder: four gateway attempts bought four identical answers and cost four requests before the worker spent five more and abandoned the game. It is the provider-404 fact wearing a different code and takes the same path — pause, cool down, come back. | M |
| OPS-19 | A **global halt** stops model calls across the system and is a *state* rather than a limit — the daily kill switch is config read at startup and cannot be flipped while the stack runs. Set by a 402, by the free-model daily cap, or by an operator, and **carries a scope**: an empty account or an operator stops everything, the free-model cap stops only `:free` seats. A credit halt lifts once a balance probe reports credit, a cap halt at the time the provider named, an operator's never. The tournament runner reads it too, so a pool does not schedule games into a stop. **No game is ended and nobody is forfeited**: the turn is not run, the job is dropped, the game stays running (invariant 11). | M |
| OPS-20 | The free tier's **daily** cap halts the harness; a provider's hot pool pauses one game. Both arrive as a 429 and they mean opposite things — the allowance is account-wide, so resting one endpoint hands the next entrant the identical refusal and a pool works through its whole field one doomed request at a time. Told apart by the message (`free-models-per-day`), because there is no code for it, and the halt expires at the moment `X-RateLimit-Reset` names rather than being probed or swept. The halt is **scoped to free models**: a paid seat never drew on the allowance. The **per-minute** cap keeps the cooldown ladder, which is right for a short wait. | M |
| OPS-21 | `status` answers **"is the harness healthy"**, which is a different question from "are the containers up" — a stack can be entirely running while every game is paused behind a rate limit, the free allowance is spent, and a pool has not moved a piece since yesterday. It reports the halt, both budgets, the queue, every live and paused game with why and for how long, and each event's settled/abandoned split. Colour is a judgement: green working, amber worth a look, red needs somebody, with everything not green repeated at the end so "is anything wrong" never means reading every line. Every section is independently fault-tolerant, because a status command that dies on one broken datastore is useless exactly when it is needed. | S |

---

## Non-functional requirements

| ID | Requirement | Target |
| --- | --- | --- |
| NFR-01 | API latency, non-LLM endpoints | p95 < 200 ms |
| NFR-02 | SSE event delivery after a ply commits | p95 < 500 ms |
| NFR-03 | Concurrent live games | ≥ 50 without degradation |
| NFR-04 | Concurrent spectators per game | ≥ 200 |
| NFR-05 | Cost per ranked model-vs-model game | < $0.50 median |
| NFR-06 | Prompt cache hit rate on turns after the first | > 80% |
| NFR-07 | Backend test coverage on `game/` and `agents/` | > 85% |
| NFR-10 | Frontend logic in `lib/` covered by unit tests | > 85% |
| NFR-11 | An automated browser suite covers the paths a person actually takes: start a game, move, resign, reload mid-game, and read a replay | exists and runs in CI |
| NFR-08 | A partial outage must never corrupt a stored game record | zero tolerance |
| NFR-09 | Time from `git push` to deployed | < 10 min |

## Key risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Runaway LLM spend | Financial | Per-game cap, per-user quota, global kill switch (AUTH-04/05/06) — built in Phase 3, before any public exposure |
| Context growth makes late-game turns very expensive | Cost, latency | Prompt caching from day one (AGENT-04); measure cost-per-ply curve and revisit if it bends badly |
| Models emit offensive trash talk under our name | Reputational | Moderation before display, stored-but-flagged (TALK-05); trash talk off by default in ranked games |
| Provider API instability mid-game | Broken games | Backoff + retry (AGENT-09), resumable game state (OPS-05) |
| Leaderboard is criticised as unfair | Credibility | Versioned, reproducible ranked config (BENCH-04); publish methodology and limitations (BENCH-10) |
| Prompt representation quietly favours some models | Validity | Treat it as an explicit experiment (VISION open question), publish the ablation |
| Long games exceed a model's context window | Silent failure | Detect the ceiling per model, record it, and fail the game explicitly rather than truncating quietly |

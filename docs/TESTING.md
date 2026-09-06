# Testing

Three suites, three bargains. All of them must be free to run and deterministic.

| Suite | Command | Needs |
| --- | --- | --- |
| Backend | `make test` · `make test-unit` (no database) | Postgres + Redis for `integration` |
| Frontend logic | `make test-web` · `make test-web-coverage` | nothing |
| Browser | `make test-e2e` (public) · `make test-e2e-all` (+ signed-in) | a running stack |

`make check` runs lint, typecheck and every suite that costs nothing. Run it before declaring work
done.

---

## The suite never calls a provider

LLM responses are replayed from cassettes in `apps/api/tests/fixtures/llm/`. **A missing cassette
raises** rather than falling back to a live call. Recording is deliberate and manual
(`make record-llm`), never something CI can trigger.

Where a provider shape cannot be reached on the free tier, hand-author the fixture and mark it
`HAND-AUTHORED` — a test enforces that marker exists.

Anything that would spend money carries the `llm` marker (`make test-llm`, opt-in) or lives in
`scripts/`, run by hand.

`agents/scripted.py` is the workhorse: it plugs in as `LlmGateway(completion_fn=...)`, so a whole
game runs with no API key, exercising the real path with only the provider replaced.
`make play ARGS="--scripted"` plays a complete game that way.

## Markers

| Marker | Meaning |
| --- | --- |
| `integration` | Needs a database (`make up`) |
| `llm` | Costs real money — never in default CI |

---

## The browser suite

Playwright, in `apps/web/e2e/`. Two projects, because the flows differ in what they need:

| | runs | needs |
| --- | --- | --- |
| `public` | `make test-e2e` — **and CI** | a running stack, nothing else |
| `signed-in` | `make test-e2e-all` | a real Clerk development instance |

Reading is open to everyone (AUTH-02), so the lobby, the catalogue, a model page and a whole replay
assert with no identity at all.

The signed-in flows sign in **for real** — a genuine Clerk session JWT, verified against real JWKS —
using a `+clerk_test@example.com` address, which a development instance treats as a test identity:
no mail is sent, the code is fixed, and **no password lives in this repository**. They are opt-in and
skipped rather than faked when the keys are absent, the same bargain the `llm` marker strikes. **CI
runs the public half only**, so the playing flow is asserted locally.

### What it starts for itself

- `agents.scripted.responsive` — a scripted opponent that **reads the board**: it asks for the legal
  moves and plays the alphabetically first, deterministically. Every other helper in that module
  replays a fixed list, which cannot answer a person.
- `scripts/worker.py --scripted` — the real worker with only the provider replaced. Its log is
  `apps/web/e2e/.auth/worker.log`, **the first place to look when the board never moves.**
- `scripts/seed_e2e.py` — plays a whole game (Scholar's Mate) through the real queue and worker so
  replay has something finished to scrub. Idempotent, and it seeds a minimal catalogue only when the
  registry is empty, which is CI — a developer's real models are left alone. It runs in
  `global-setup.ts` **before** the worker starts, and that order is load-bearing.
- `scripts/seed_e2e_user.py` — creates and funds the test account. New users get no credits by
  design (AUTH-11), so an unattended suite could otherwise not start a game.

### Rules that cost a debugging pass each

**It tests a *running* stack, so a stale process gives a stale answer.** A leak the browser reported
as real turned out to be an API server started hours earlier, serving events without the redaction
the worker beside it was already writing. Restart the API and the worker after backend changes, or
run them with reload.

**Only one worker may consume the queue here.** Not because two is unsafe — the queue is a Redis
Streams consumer group, so identical workers share the load (`WORKER_REPLICAS`). The rule is about
workers that are *not* identical: a scripted one racing a real one is a coin toss over who plays
each turn. Seeding used to run as a Playwright project, i.e. *after* the background worker had
started, and the two fought over the seeded game — locally the seed won and the game came out at its
expected seven plies, in CI it lost and ran to fifteen. **If you are running `make worker` by hand,
stop it first.**

**CI serves a production build; `make web` serves `next dev`.** Not the same target. A production
build prefetches its own routes over RSC, so `localhost:3010/models?_rsc=…` appears in the network
log there and never under `next dev` — which is why an assertion about "no request per keystroke"
passed locally and failed in CI. To reproduce CI exactly:

```
cd apps/web && pnpm build && pnpm start     # not pnpm dev — and on 3010, not another port
pnpm exec playwright test --project=public
```

**On 3010 specifically.** The API allows one CORS origin (`cors_origins`, default
`http://localhost:3010`), so serving the front end anywhere else makes every browser-side fetch fail
and the suite reports it as missing UI. That is the suite working.

**Headless Chromium is a second download, and its absence looks like sixteen failures.** Since
Playwright 1.49 headless mode launches `chromium_headless_shell`, a separate binary from the full
`chromium` build — so a machine that has one may not have the other, and every test fails at
`browserType.launch` with *"Executable doesn't exist"*. The `playwright` MCP server ships its own
browser and does **not** satisfy the test runner. Install it:

```
cd apps/web && pnpm exec playwright install chromium
```

The full build works too if you would rather not download a second one — `use: { channel: "chromium" }`
selects it, and it is what this repo was verified against. Do not commit that override: CI installs
the shell, and the shell is what CI runs.

**Migrations are part of the running stack.** `global-setup.ts` seeds through the real queue, so a
database behind `head` fails there — `relation "tournaments" does not exist`, reported as a setup
error rather than a test failure. `make migrate` first if you have just changed branches.

### Four traps

1. **A message's content is not always a string.** By the time it reaches the provider, the
   prompt-caching path may have wrapped it into `[{"type": "text", ...}]` so a `cache_control` marker
   can ride along (ADR-0003). Parsing it as a string made the scripted opponent ask for the legal
   moves twenty times in one turn, in silence.
2. **`/ w /` is not "the model has replied".** The starting position is white-to-move too, so the
   wait is already satisfied and every later assertion reads a board that has not moved.
3. **The first `aria-expanded="false"` on a signed-in page is the account button**, not a turn.
   Clicking it opens the Clerk user menu over the page and every later click fails on an element it
   has covered. Scope fold selectors by their text.
4. **Not every URL containing `/models` is an API call.** Assert against the API origin, or a router
   prefetch counts as a request the page did not make.

---

## Coverage

Frontend `lib/` coverage is measured *and* enforced (`make test-web-coverage`, NFR-10). `api.ts` and
`site.ts` are excluded from that floor and covered by the browser suite instead: unit-testing fetch
wrappers means mocking `fetch` and then asserting the mock.

Components are covered by Playwright rather than a jsdom stack.

`game/` is held at high coverage and is **pure by enforcement** — a test asserts it imports nothing
from `db/`, `agents/` or `api/`.

## A read endpoint is measured when it is written

**How the leaderboard reached 295 queries for 37 games: nothing was ever wrong.** Each addition read
one more thing per game, every one was correct, and no test could tell 8 queries from 295. It was
found by a person saying the site felt slow, months later, by which point four separate call sites
were looping over the same archive.

So a new endpoint that returns a **list** — or anything derived from every game — is measured on the
way in, not after somebody complains:

```python
statements: list[str] = []

def record(conn, cursor, statement, *args):
    statements.append(statement)

sa.event.listen(db.bind.sync_engine, "before_cursor_execute", record)
```

**Assert against growth, not a magic number.** Build the endpoint's answer at one row, then at
several, and assert the count did not move. A fixed bound rots the moment a legitimate join is
added; `seven == one` stays true for any correct implementation and fails for every per-row read.
`test_the_leaderboard_costs_a_fixed_number_of_queries` is the worked example, and it was confirmed
to fail — 8 queries at one game, 14 at seven — when the loop was put back.

Two things make this cheap to get right the first time:

- **Read set-wise, then group in Python.** One query per table, keyed by `(game, player)`, beats a
  loop that is easier to write. `bench.service.scan` is the shape.
- **Ship a summary; make the payload opt-in.** `/games/{id}/turns` shipped every verbatim provider
  payload to every caller because the endpoint that had them was the one that already existed.
  Nothing read them. If a field is only wanted behind a click, it belongs behind a query parameter
  or its own route.

**Timing tests do not do this job.** They pass on a fast machine with the bug still in place, and
they fail on a loaded one with nothing wrong. Count statements.

**And ask whether the work belongs on the read at all.** The leaderboard was not merely reading
per game — it was recomputing the entire ranking on every request, for four pages, two of which
display no rating. Making the computation cheap was the smaller half; moving it off the read path
and behind a fingerprint was the fix ([ADR-0032](adr/0032-the-leaderboard-is-stored-not-recomputed-per-request.md)).
A cached result needs a test that it **cannot be served stale** — `test_snapshot.py` asserts that a
new game, a tampered fingerprint and an empty table all rebuild rather than publish an old number.

## Writing a test that would have caught the bug

The habit this codebase holds to: when a test is written for a fix, **verify it fails without the
fix**. Several tests in `tests/` say so in their docstrings, and more than one bug was found because
an assertion that could never fail was noticed — comparing an `Entrant` to a `str`, or asserting a
state the fixture already guaranteed.

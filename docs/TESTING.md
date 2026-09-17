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
| `mobile` | `make test-e2e` / `make test-e2e-mobile` — **and CI** | a running stack, nothing else |
| `signed-in` | `make test-e2e-all` | a real Clerk development instance |
| Lighthouse | `make lighthouse` — **and CI** | a running API; it starts its own web server |

Reading is open to everyone (AUTH-02), so the lobby, the catalogue, a model page and a whole replay
assert with no identity at all.

`mobile` is the same public pages at 390px, and it exists because **a layout failure is invisible
from a desk**. Every assertion in it is a measurement that was failing on production: a pairing's two
model names splitting one row and getting **37px** each against the 310px the longest needs; a
player's nameplate losing a quarter of its name to a huddle of captured pieces; the leaderboard's
rating column parked off-screen behind a horizontal scroll nothing announced. None of it is
reachable from vitest — `vitest.config.mts` keeps components out on the grounds that layout is
Playwright's, and this is the project that makes that true rather than merely stated.

It runs Chromium (`devices["Pixel 7"]`), not a WebKit phone, because CI installs `chromium` alone
and what is asserted — width, touch targets, breakpoints — is not engine-specific.

**Two traps, both hit while writing it.** A layout assertion can pass against the broken layout if
the fixture is too short: `anthropic/claude-fable-5` fits at 390px either way, so "the name is not
truncated" was green before the fix and proved nothing. Assert the *structural* property that
changed — the name now has the row, the two names are on separate lines — and it holds whatever the
data says. And the suite's own pairings are mostly **queued**, so they render without the `<a>` a
selector written against production would go through; that one returned an empty list and passed.

The signed-in flows sign in **for real** — a genuine Clerk session JWT, verified against real JWKS —
using a `+clerk_test@example.com` address, which a development instance treats as a test identity:
no mail is sent and the code is fixed. They are opt-in and skipped rather than faked when the keys
are absent, the same bargain the `llm` marker strikes. **CI runs the public half only**, so the
playing flow is asserted locally.

`auth.setup.ts` gets that session by driving `window.Clerk` directly, which is right for the play
tests and **asserts nothing about our own screens** — it never touches a field on them.
`account.spec.ts` is what does, walking a throwaway identity through sign-up, the profile, a display
name, signing out and signing back in. It found two bugs that were live on the site on the day it
was written, both of them unreachable from the signed-out state anyone had been checking by eye.

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

### Five traps

1. **A message's content is not always a string.** By the time it reaches the provider, the
   prompt-caching path may have wrapped it into `[{"type": "text", ...}]` so a `cache_control` marker
   can ride along (ADR-0003). Parsing it as a string made the scripted opponent ask for the legal
   moves twenty times in one turn, in silence.
2. **`/ w /` is not "the model has replied".** The starting position is white-to-move too, so the
   wait is already satisfied and every later assertion reads a board that has not moved.
3. **Waiting for a folded turn in a live game waits for ever.** `EventStream` unfolds the *focused*
   turn without being asked, and a human game has exactly one model turn — which is the focus. A
   selector written against `aria-expanded="false"` timed out for as long as that default existed,
   in the one project CI does not run. Open the turn *if* it is folded, and wait on its steps rather
   than on the click: an assertion that something is **absent** cannot fail against a turn that
   never opened.
4. **Not every URL containing `/models` is an API call.** Assert against the API origin, or a router
   prefetch counts as a request the page did not make.
5. **`signOut` ends the sessions of the client, not of the tab.** Every context built from
   `STORAGE_STATE` restores the same client cookie, so a test that signs out revokes the session
   every later test is still using. `account.spec.ts` creates and deletes a throwaway identity for
   exactly this reason.

---

## Lighthouse budgets

`make lighthouse`, and CI runs it beside the browser suite. `apps/web/lighthouserc.cjs` holds the
config and the reasoning; the short version is **what it refuses to assert**.

This project has already deleted two wall-clock tests for the reason that matters here — a timing
assertion that fails on a busy machine and passes on a quiet one teaches people to rerun CI, and
the next real regression is rerun away with it. **A Lighthouse performance score is that assertion
wearing a different hat**: LCP on a shared runner moves by seconds between runs of identical code.

So the suite splits, and the split is the design:

| | |
| --- | --- |
| **Asserted** | accessibility, best practices, SEO; contrast, accessible names, tap targets, heading order; page weight and unused JavaScript. All of them audit the DOM or the network, and give the same answer on a loaded machine as an idle one |
| **Recorded only** | the performance score, LCP, FCP, TTI, Speed Index, server response. Uploaded as a CI artifact on every run so a trend is visible — never a reason a merge is blocked |

Three things worth knowing before changing any of it:

* **It measures the whole public site, with Clerk.** Twelve routes, including a real game and a
  real tournament taken from the browser suite's fixtures. It used to measure four routes with the
  Clerk keys cleared, because a development tenant was 55% of the page; owning the auth screens
  removed the reason to look away, and widening it found two things in the first run — an audit
  asserted that no longer exists, and `/sign-in` scoring 0.63 on SEO because `robots.txt`
  deliberately keeps it out of the index.
* **`categories:best-practices` is a warning, not a gate,** and only because a *development* Clerk
  tenant fails three audits that production passes: its cookies, its unreachable telemetry endpoint,
  and the issues its dev mode logs. Production measures 100. The best-practice audits that are ours
  are asserted individually, so nothing we control is unguarded.
* **`lhci` starts and stops its own server.** `next start` reads `.next` once at boot, so rebuilding
  while it runs leaves it serving yesterday's HTML with today's stylesheet. Measuring that produced
  eight failures on a tree that passes cleanly.
* **Every threshold is the measured value, not a round number.** A budget above the current figure
  permits a regression; one below it is red on arrival. They only ever tighten.

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

## Assert the claim, not the change

A test written against a *fix* can only assert the fix. ADR-0040 removed the `checkmate` flag from
`get_legal_moves` and the test asserted `"checkmate" not in flags` — which is a restatement of the
diff. The claim was *"a model cannot read the mate off the list"*, and the `#` was still in the SAN
string: `Qxf7#`, one `#` among forty-five alphabetically sorted moves, and the model played it the
first night it shipped.

The rule that falls out: **a test for a rule about what a model can see scans the whole payload for
the thing being withheld, not the field that was edited.** It generalises — testing the era
*display* would have missed the matchmaker reading a completed round robin from a task nobody was
playing (ADR-0043), which is the same mistake about a different subject.

## The invariants are checked against games that were played

`tests/agents/test_the_invariants_hold.py` plays games through the real turn loop with scripted
models that behave the way real ones do — calling tools after moving, truncating with and without a
tool call, going silent, repeating, playing illegal moves — and then asks the database the
questions CLAUDE.md's invariants ask.

It exists because every test in the suite asserts one thing about one path, and the failures that
reached production were properties of a *whole game* that no single test was watching. The
`max_closing_rounds` ordering bug corrupted **242 transcript rows across 14 seats** with `make
check` green throughout; reintroducing it fails three tests in that file.

A new invariant belongs there rather than in a test of its own.

## The cassette corpus is recorded from production

`make harvest-cassettes` walks recent games on the public API and records one cassette per distinct
response *shape* — sixteen of them, across five vendors. `make harvest-cassettes ARGS="--game <id>"`
reads one named game instead, for the shapes of something that has just broken.

The scripted double can only produce shapes somebody thought to write, and every parsing failure
that reached production was a shape nobody thought to write. The corpus does not prevent the next
unknown one; it stops the known ones coming back.

## Writing a test that would have caught the bug

The habit this codebase holds to: when a test is written for a fix, **verify it fails without the
fix**. Several tests in `tests/` say so in their docstrings, and more than one bug was found because
an assertion that could never fail was noticed — comparing an `Entrant` to a `str`, or asserting a
state the fixture already guaranteed.

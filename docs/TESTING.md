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

## Writing a test that would have caught the bug

The habit this codebase holds to: when a test is written for a fix, **verify it fails without the
fix**. Several tests in `tests/` say so in their docstrings, and more than one bug was found because
an assertion that could never fail was noticed — comparing an `Entrant` to a `str`, or asserting a
state the fixture already guaranteed.

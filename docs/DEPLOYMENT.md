# Deploying Chessmark

Everything runs in containers, including the commands that manage tournaments. **There is no proxy
and no TLS in this stack** — the API and web ports are published for a reverse proxy managed
outside it, so certificates, domains and redirects are not its business.

```
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## The services

| service | what it is | notes |
| --- | --- | --- |
| `postgres`, `redis` | datastores | from `docker-compose.yml` |
| `migrate` | `alembic upgrade head`, once | runs to completion, then exits |
| `api` | FastAPI | published on `API_PORT` (8010) |
| `web` | the built Next.js app | published on `WEB_PORT` (3010) |
| `worker` | plays turns | **exactly one** |
| `tournament` | ticks an event | also the management CLI |
| `catalogue` | refreshes the model registry | every `CATALOGUE_INTERVAL_HOURS` (12) |

`api`, `worker`, `tournament`, `catalogue` and `migrate` are **one image**. They share every line
of code — the worker is the API's own turn loop, the tournament runner drives the same
orchestration — so building them separately would give them several chances to drift apart at the
point where they must not.

## `./chessmark` — the stack without a toolchain

`make` is for a development machine. On a server, where Docker is all there is, `./chessmark` runs
everything inside a container: no uv, no node, and no remembering which compose files to combine.
`./chessmark help` lists the commands; `./chessmark help <command>` gives examples.

| | |
| --- | --- |
| `up` `down` `ready` `logs` `restart` | the stack |
| `status` | containers, plus the halt, budgets, queue, live and paused games, and every event |
| `deploy` | pull the published images, migrate, restart, check `/ready` |
| `workers N` | how many turn workers to run (`WORKER_REPLICAS`) |
| `catalogue` `endpoints` `models` `prune` | the model registry |
| `latency <game-id>` `resume <game-id>` `repair` `repair-forfeits` | one game, and the records behind it |
| `halt` | stop every model call, or resume; `--clear` lifts it |
| `tournament …` `standings <slug>` | events |
| `credits` | grant or revoke; `--show` prints a balance with its ledger |
| `psql` `sql` `backup` `migrate` | the database |

Four details that are deliberate rather than incidental:

- **`deploy` is the whole sequence** — pull, **drain the workers**, migrate, restart, check
  `/ready`. There is no half of it worth running alone.

  The drain is not tidiness. Every `ALTER TABLE` takes **ACCESS EXCLUSIVE**, including a
  catalogue-only `SET DEFAULT` that does microseconds of work, and that conflicts with the ACCESS
  SHARE any plain `SELECT` holds. The worker keeps **one transaction open for a whole turn**
  (NFR-08) and a free model's turn runs to 442 seconds — so `migrate` queued behind it and the
  deploy looked hung. A queued ACCESS EXCLUSIVE also sits at the head of the lock queue and blocks
  everything behind it, so the API stalled too.

  Stopping a worker mid-turn is safe by design: the turn is one transaction and rolls back whole,
  and its job is redelivered (`expected_ply` idempotency, ADR-0007). If the migration fails, the
  workers come back on the old image and `deploy` returns non-zero.

  **Migrations wait at most `ALEMBIC_LOCK_TIMEOUT_SECONDS` (default 10) for a lock**, then fail
  loudly. `lock_timeout` bounds only the wait, never the work, so a slow backfill is unaffected —
  raise it for one that legitimately needs to wait behind a reader. If a migration does fail this
  way, find the holder:

  ```
  ./chessmark sql <<'SQL'
  SELECT pid, state, now() - xact_start AS txn_age, left(query, 80)
  FROM pg_stat_activity
  WHERE datname = current_database() AND pid <> pg_backend_pid()
  ORDER BY xact_start NULLS LAST;
  SQL
  ```
- **`restart` uses `--force-recreate`.** A container that once failed to bind its port can come back
  running-but-unpublished: healthy inside, unreachable outside, with `docker port` empty. `start` does
  not fix that; recreating does.
- **`sql` runs with `ON_ERROR_STOP=1`**, and both it and `backup` read the credentials from *inside*
  the container, so there is no second copy of `POSTGRES_USER` to drift.
- **`credits` resolves a person the same way the API does** (`db.users.resolve_user`, shared with
  `POST /admin/credits`), and an email we do not hold is asked of Clerk — so an invitation can be
  pre-funded for somebody who has never signed in. `core.clerk.get_directory` lives in `core/` for
  that reason: `db/` importing `api/` would invert the layering for one cached client.

## Managing tournaments

The `tournament` service's entrypoint is the script, so management reads naturally:

```
docker compose run --rm tournament field --free
docker compose run --rm tournament create --name 'Free Models' --slug pool-free \
    --free --format pool --active-from 06:00 --active-until 20:00
docker compose run --rm tournament standings pool-free
docker compose run --rm tournament pause pool-free --abort-live
```

The long-running `tournament` container ticks whichever slug `TOURNAMENT_SLUG` names. One container
per event you want running.

## The catalogue keeps itself current

`refresh_catalogue.py` registers what OpenRouter offers and then sweeps each model's endpoints —
the two halves are useless apart, because a model with no endpoint rows has no contestants and
cannot be picked. It opens with *"built to be scheduled rather than remembered"*, and for a long
time **nothing scheduled it** (OPS-23): it ran when somebody typed the command. Prices set the
spend caps *and* what a user is charged in credits (ADR-0016), so every day nobody remembered was a
day of wrong caps and wrong prices.

The `catalogue` service is the schedule. It refreshes at start-up and then every
`CATALOGUE_INTERVAL_HOURS` (12 by default), and it spends nothing — `/models` and `/endpoints` are
metadata, not inference, so the daily free allowance and the credit balance are untouched.

A **service rather than a host timer**, deliberately. A crontab lives nowhere in this repository, a
rebuilt server loses it silently, and nobody discovers it is missing until a price is six months
old. Here, `docker compose ps` is how you check the schedule is running and `./chessmark logs
catalogue` is how you read what it did.

**A pass that fails does not stop the schedule.** OpenRouter down, the network gone, Postgres
restarting — the failure is logged and the loop waits for the next interval. Exiting would hand it
to the restart policy, which puts the process straight back into the same failure; this stack has
already watched its tournament runner exit-loop for exactly that reason. A pass that reaches
OpenRouter and gets an empty catalogue **refuses to touch the registry** rather than disabling every
model at once.

Run one by hand any time — it is idempotent:

```
./chessmark catalogue                 # once, now
./chessmark catalogue --skip-endpoints   # the fast half only
```

## How many workers

**Identical workers share the load; different ones fight.** The queue is a Redis Streams consumer
group, so a job goes to exactly one consumer — running several copies of the same image shards the
work rather than duplicating it. `WORKER_REPLICAS` (or `./chessmark workers 3`) sets the count.

This is worth having, because **a worker plays one turn at a time, start to finish**. A free model's
turn is 17–38s typically and 442s at worst, so one of those blocks whatever is behind it — including
a human's game, which is how the head-of-line blocking was noticed.

**Every worker also runs a reconciler, and those *would* duplicate** — two of them seeing the same
free concurrency slot and each filling it. The sweep therefore takes a Redis `SingleFlight` lock with
a **TTL** rather than a real lock: a missed sweep costs a minute, and a lock nobody can release costs
everything after it.

What must not happen is workers that are **not** identical. A scripted one racing a real one is a
coin toss over who plays each turn, with different providers — that mistake cost a debugging pass in
the browser suite, where a seeded seven-ply game came out at fifteen. See
[TESTING.md](TESTING.md#rules-that-cost-a-debugging-pass-each).

## The API has two addresses

This is the one that will catch you, because the site looks perfectly healthy while it happens.

`NEXT_PUBLIC_API_URL` is what a **browser** uses. It is baked into the client bundle at build time,
cannot be supplied at run time, and must match `cors_origins` on the API — which allows exactly one
origin.

`INTERNAL_API_URL` is what the **web container** uses when rendering a page on the server. In
Docker these are not the same place: `http://localhost:8010` reaches the API from a person's
browser and reaches *the web container itself* from inside it. With only the public URL set, every
server-rendered page fetched nothing and rendered its empty state — a site that was up, returned
200 everywhere, and had no data on it.

`INTERNAL_API_URL` is deliberately not prefixed `NEXT_PUBLIC_`, so it exists only on the server. In
a browser it is undefined and the public URL is used, which is what lets one constant in
`lib/api.ts` serve both sides.

## The three URLs that must agree

`NEXT_PUBLIC_API_URL` is **baked into the client bundle at build time** and cannot be supplied at
run time. It must be the URL a *browser* uses — not `http://api:8000`, which no browser can reach —
and it must match `cors_origins` on the API, which allows exactly one origin.

Get this wrong and every browser-side fetch is refused. It does not present as a network error: the
page renders and its data never arrives, which reads as missing UI. It cost a debugging pass when
the app was served on 3011 instead of 3010.

So when the proxy is in front:

```
NEXT_PUBLIC_API_URL=https://api.example.com
CORS_ORIGINS=https://example.com
```

and the proxy sends `example.com` to `web:3000` and `api.example.com` to `api:8000`.

`NEXT_PUBLIC_SITE_URL` is the third, pointing the other way: the site's *own* origin, read by
`metadataBase`, the OpenGraph card and the sitemap. Unlike the other two it does not break
anything when it is wrong, which is worse — the site works and every canonical URL, share card and
indexed link it publishes says `http://localhost:3010`.

```
NEXT_PUBLIC_SITE_URL=https://example.com
```

## Build inputs that are not source files

Three build failures, three files that had to be in the image context but are not obviously code.
Worth knowing before adding a fourth:

- **`apps/api/README.md`** — `pyproject.toml` declares `readme = "README.md"`, and hatchling
  *validates* that the file exists before it will build the package. It is metadata, not
  documentation, as far as the build is concerned.
- **`apps/web/pnpm-workspace.yaml`** — carries `allowBuilds`. pnpm 10+ refuses to run a
  dependency's install script unless told, and **errors when it cannot prompt**, which is every
  non-interactive install. Without this file the container build fails on `unrs-resolver` while a
  developer's terminal installs silently. The failure only exists outside a TTY, so local
  development never reveals it.
- **`apps/web/public/`** — `output: "standalone"` does not copy `public` or `.next/static`; it
  assumes a CDN serves them. There is no CDN here, so both are copied in and `server.js` serves
  them.
- **`/.dockerignore`, and only that one.** Both images build with the repo root as context, and
  Docker reads `.dockerignore` from the *context* root rather than from beside the Dockerfile. A
  file at `apps/web/.dockerignore` is inert. Without the root one the host's `node_modules` — a
  tree of pnpm symlinks — was copied over the flat one installed in the image, and the build died
  trying to replace a directory with a symlink. Three rebuilds went into `--no-cache` and a full
  builder prune before the context turned out to be the problem.

## Runtime secrets the web container needs

Clerk needs **both** keys and they arrive by different routes. The publishable key is baked into
the client bundle at build time; `CLERK_SECRET_KEY` is a server-side secret that must never be, so
it comes in at run time via `env_file`.

Without it `clerkMiddleware()` throws — and a throwing proxy takes every route with it, so the
whole site answers 500. That is the same failure a fresh clone hit before `src/proxy.ts` learned to
check for a key, and it is worth remembering that the guard there tests the *publishable* key while
the middleware also needs the *secret* one.

And one command that had to change: the web build calls `node_modules/.bin/next` directly rather
than `pnpm exec next`. `pnpm exec` re-verifies the install first and, finding a tree it did not
create, tries to remove `node_modules` — which it refuses to do without a TTY.

## Backups, audits and drills

```
make backup ARGS=--verify   # dump, restore into a scratch database, compare, drop it
make audit                  # pip-audit over the production lockfile, plus pnpm audit
make recovery-drill         # lose each container in turn, check nothing was lost
```

`--verify` compares **row counts per table**, not `pg_dump`'s exit code. The failure worth catching
is a dump that runs happily for months and turns out to be missing a schema.

The drill kills each container and brings it back. It does not test auto-restart, because no
restart policy covers `docker kill` — Docker reads that as operator intent, and the same is true of
`always`. Policies cover *crashes*, which is the case that actually happens.

## Stopping everything

```
./chessmark halt                      # is it on, and why?
./chessmark halt "paying for a bug"   # stop every model call, now
./chessmark halt --clear              # start again
```

Two switches stop spending and they are different things. The **daily kill switch** is a limit —
`GLOBAL_DAILY_USD_BUDGET` in config, compared against a counter that resets at UTC midnight, and
changing it means editing `.env` and restarting. The **halt** is a state: it stops everything now,
it stays until something lifts it, and it can be set while the stack runs (OPS-19).

**Games are left running and nothing is forfeited.** A halted turn is not run and its job is
dropped; the game stays `RUNNING` and the reconciler picks it up once spending is possible again.
A model must never lose a game because we stopped the harness (invariant 11) — so a halt is safe
to use freely, including in the middle of a tournament.

**Free games stop too.** OpenRouter's own documentation says a 402 applies to free models when the
balance is negative, so exempting them would mean spending the day rediscovering the same refusal
thirty games at a time.

### It can set itself

A **402** from OpenRouter — *"Your account or API key has insufficient credits"* — sets the halt
automatically, and that one **lifts itself**: the reconciler probes the account balance every five
minutes and resumes once there is credit. Top up and walk away; you do not need to run anything.

The **free-tier daily cap** sets it too, and lifts even more cleanly. It arrives as a 429 reading
*"Rate limit exceeded: free-models-per-day"* — account-wide, so every free model refuses at once —
and `X-RateLimit-Reset` says when it goes. The halt is written with that as its expiry, so nothing
probes and nothing sweeps: the key simply runs out. `./chessmark halt` shows the time it will lift.
If you see this most days, the pool is running hotter than the allowance and the answer is fewer
concurrent games or a paid entrant, not a bigger reserve.

A halt you set by hand never lifts itself. Somebody meant it, and a probe deciding otherwise would
be the system overruling its operator, so `--clear` is the only way back.

The first halt wins. Setting one over an existing halt reports what is already there rather than
replacing it, so a 402 cannot quietly overwrite the reason you typed.

### When it is on

`./chessmark halt` with no arguments prints the reason, who set it, how long ago, and — for a
credit halt — what the balance was at the time and that it will lift on its own. Worker logs carry
`harness halted (credits: …); not running <game> at ply N` once per attempted turn.

## Deploying a change that needs a data repair

Most deploys are `./chessmark deploy` and nothing else. This section is for the ones that are not:
a schema change plus a repair of records the old code wrote. The pattern generalises, and the
worked example is the ADR-0021 / ADR-0022 release, because it has one of everything.

**The order is not arbitrary.** Migrate before the new code runs, repair after it is running, and
resume last — a game reopened before the repair simply fails the same way again.

### 1. Back up first, and check the dump is real

```
./chessmark backup
```

A repair rewrites rows. `--verify` on a development machine compares row counts per table; on the
server the dump's size is the check that catches the failure that actually happens, which is a
backup that has been running happily for months and turns out to be empty.

### 2. Deploy

```
./chessmark deploy
```

Pulls, **stops `worker` and `tournament`**, migrates, restarts, and waits on `/ready`. The drain is
the part that matters: every `ALTER TABLE` takes `ACCESS EXCLUSIVE`, a worker holds one transaction
open for a whole turn, and a free model's turn runs to 442 seconds — so a migration queues behind
it and then blocks the API behind *itself*. Stopping a worker mid-turn is safe by design: the turn
is one transaction, it rolls back whole, and its job is redelivered (`expected_ply`, ADR-0007).

If the migration fails, `deploy` brings the workers back on the **old** image and stops. That is the
intended outcome — a half-migrated database serving new code is the one state worth refusing.

To migrate without restarting anything else: `./chessmark migrate`.

**The two migrations in this release** are both additive column adds, so they take the lock for
microseconds and need no backfill:

| Migration | Column | Why |
| --- | --- | --- |
| `bd3b3902caa2` | `players.last_prompt_tokens` | the measured prompt size, carried between turns (AGENT-19) |
| `09d20bf76afb` | `transcript_messages.trimmed_at` | a tool result kept in the request with its content elided (AGENT-20) |

Both default to a value that means "nothing known yet", so a game in flight across the deploy
behaves correctly on its next turn with no intervention.

### 3. Repair the records the old code wrote

```
./chessmark repair            # reports; writes nothing
./chessmark repair --write    # supersedes them
```

`repair` finds assistant messages carrying neither content nor tool calls. The transcript is
append-only (ADR-0003), so one such row refuses **every later turn of that seat** — the single 400
that no pause, retry or resume can ever clear. The new code cannot write one and filters any that
exist, so this is about cleaning the record rather than restoring play.

It **supersedes**, never deletes: `superseded_at` is set, `content` is untouched, and the row keeps
its place. The record stays verbatim (invariant 3) and only the request changes.

Read the dry run before writing. It names the game, the seat and the model for every row, and a
count of rows far larger than you expected is a reason to stop rather than to add `--write`.

```
./chessmark repair-forfeits            # reports; writes nothing
./chessmark repair-forfeits --write
```

A different repair, and the one to reach for when a **result is already correct**. `Player.forfeited`
is a stored verdict and a published one — it is the leaderboard's forfeits column — and it drifted
two ways: a harness stop set it (`BUDGET_EXCEEDED` ends the game, so it travelled as a forfeit), and
a resume never cleared it. Two free-pool games were budget-stopped, reopened, and played on to a
genuine checkmate and a genuine threefold draw, and both models carried a forfeit nothing in their
play had earned ([ADR-0024](adr/0024-endpoint-output-ceilings-are-not-findings.md)).

It changes **no result and reopens no game** — the rule is derived from the game record, which is
the authority (invariant 1): a seat is forfeited exactly when its game ended by a forfeit against
that seat. Reach for `resume` when the *ending* is wrong; reach for this when only the flag is.

It reports what it would clear and what it would set **apart**, because they are not the same risk:
clearing drops a claim we cannot support, setting adds one to a published column.

### 4. Reopen what was abandoned

```
./chessmark resume <game-id>
```

Only endings **we** imposed can be reopened — a budget, a ply cap, a provider we could not reach,
an answer cut off by an output ceiling ([ADR-0024](adr/0024-endpoint-output-ceilings-are-not-findings.md)).
A checkmate is final and so is a forfeit: both are findings about a player, and a script that could
replay one is a script that could replay a bad result until it improved. The refusal is the point
of the command existing rather than a hand-written `UPDATE`.

Reopening also **clears a stale forfeit on the seat**. The flag is a verdict written by the ending
being reopened, and it is published — it is the leaderboard's forfeits column — so leaving it
behind is the same mistake as leaving a resumed pairing scored.

```
./chessmark resume <game-id> --max-usd 2.50      # raise the ceiling that stopped it
./chessmark resume <game-id> --max-plies 400
./chessmark resume <game-id> --unclaimed-draw    # a threefold nobody was told about (ADR-0020)
```

A worker must be running for a resumed game to move; `resume` says so, and `./chessmark status`
confirms it.

**Resume last.** A game reopened before its transcript is repaired plays one turn, hits the same
400, and is abandoned again — this time with two abandonments in its log instead of one.

### 5. Watch it, briefly

```
./chessmark status
./chessmark logs worker
./chessmark standings pool-free
```

What you are looking for in the first few turns:

- `compacting … folding N, trimming M, keeping K` — the ladder running, with the numbers that were
  previously invisible (ADR-0021). `trimming 0, folding 0` repeated is the shape of the bug that
  release fixed and would mean it had not taken.
- `pausing … (pause 1, 0.0h of 24h used)` — ordinary. Free pools are hot; that is what a pause is
  for.
- `dropping job for … another worker is advancing it` — expected with more than one worker, and
  not a failure (ADR-0022). Frequent enough to be noise means the stale threshold wants raising
  again.

### Rolling back

The images are tagged `:<sha>`, so a rollback is a pull of the previous tag and a restart. **The
migrations are not rolled back with them.** Both columns in this release are additive and unread by
the old code, so an old image runs against the new schema without noticing — which is the property
that makes the rollback safe, and it is worth checking per release rather than assuming. A
migration that drops or renames does not have it, and there the rollback is the backup from step 1.

A repair cannot be rolled back by redeploying, because it changed data rather than code. Undoing
one means clearing `superseded_at` on the rows it set, which is why the dry run exists and why the
backup comes first.

## Continuous deployment

Push to `main` publishes `ghcr.io/<repo>-api` and `-web`, each tagged `:latest` and `:<sha>`. The
deploy job then SSHes to `vars.DEPLOY_HOST`, pulls, runs `migrate` to completion, restarts, and
waits on `/ready`. With no host configured it is **skipped rather than failed**.

Set repository variables `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`, `HEALTH_URL`,
`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SITE_URL`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, and the secret
`DEPLOY_SSH_KEY`:

```
gh variable set NEXT_PUBLIC_API_URL  --body https://api.example.com
gh variable set NEXT_PUBLIC_SITE_URL --body https://example.com
```

The two `NEXT_PUBLIC_` URLs are **required, and the publish fails without them.** They are read at
build time and baked in, so an unset one is not a default — it is a web image hard-wired to
`localhost`, which serves a site that is up, answers 200, and fetches nothing from anybody's
browser. The check runs after the backend image, which is environment-independent and worth
publishing either way.

**An unset variable is not absent by the time it arrives.** `${{ vars.X }}` interpolates to `""`,
`docker build` forwards that as an empty build arg, and `process.env.X ?? "…"` keeps it — an empty
string is not nullish. That is what broke the first publish from `main`: `next build` inside the web
image made every server-side fetch a *relative* one, Next's patched fetch neither resolved nor
rejected it during the prerender but hung, and the build died on a 60-second timeout for
`/sitemap.xml` that named no URL anywhere. The backend image published; the web image never did, so
the server had nothing to pull. `lib/env.ts` now treats blank as unset — the same rule Compose's
`${VAR:-default}` has always followed, which is why nothing local ever showed it — `lib/api.ts`
gives every server-side read a ten-second ceiling, and CI builds both images on every pull request
with **no build args at all**, which is the exact shape that failed.

**The deploy half has never run.** It is written from the documented behaviour of the actions it
uses and stays unproven until a server exists.

## First deploy

1. `.env` at the repo root, with `DATABASE_URL`, `REDIS_URL`, Clerk keys and `OPENROUTER_API_KEY`.
2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
3. `docker compose run --rm api python /app/scripts/seed_models.py` — the registry starts empty.
4. `docker compose run --rm api python /app/scripts/refresh_endpoints.py` — a model has no
   contestants, and so cannot be played, until its endpoints are known.
5. Grant yourself credits: new accounts hold none by design (AUTH-11).

**Clerk production is a different instance from Clerk development.** Different user table,
different JWKS. Every account in your dev instance — including yours, and its credits — does not
exist there. The first sign-in starts from an empty `users` table.

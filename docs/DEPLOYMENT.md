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

`api`, `worker`, `tournament` and `migrate` are **one image**. They share every line of code — the
worker is the API's own turn loop, the tournament runner drives the same orchestration — so
building them separately would give them three chances to drift apart at the point where they must
not.

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

## Only one worker

A job goes to whichever worker reaches it first, so a second is not redundancy — it is a coin toss
over who plays each turn. `deploy.replicas: 1` says so, and scaling it is a correctness bug rather
than a capacity decision. That mistake already cost a debugging pass in the browser suite, where a
seeded seven-ply game came out at fifteen.

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

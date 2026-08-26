# The web image.
#
# Three stages, because a production image should contain the built app and nothing that built it:
# no pnpm store, no dev dependencies, no source. `output: "standalone"` does the tracing.

FROM node:24-slim AS deps
WORKDIR /app
RUN corepack enable
# `pnpm-workspace.yaml` is not optional here. It carries `allowBuilds`, and pnpm 10+ *errors*
# on an unapproved install script when it cannot prompt — which is every container build. Without
# this file the install fails on `unrs-resolver` while a developer's terminal installs silently.
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
# Cached separately from the source: dependencies change far less often than pages do.
# `--node-linker=hoisted` gives a flat `node_modules` rather than pnpm's symlinked store.
# `output: "standalone"` traces the files each route needs and copies them, and that tracing does
# not follow the store's symlinks reliably — the image built, started, and died on a missing
# `@swc/helpers` that Next had traced but not copied. A flat tree is what the tracer expects.
RUN pnpm install --frozen-lockfile --node-linker=hoisted

FROM node:24-slim AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./

# Read at build time and baked into the client bundle, so it cannot be supplied at run time the
# way a server-side variable can. A wrong value here is a site whose every browser-side fetch is
# refused by CORS — which the browser suite reports as missing UI, and which cost a debugging pass.
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY

# The binaries are called directly rather than through `pnpm exec` / `pnpm build`. `pnpm exec`
# re-verifies the install first and, finding the tree it did not create itself, tries to remove
# `node_modules` — which it refuses to do without a TTY, so the build dies rather than proceeding.
# Invoking the binary skips that check entirely; the dependencies are already exactly what the
# lockfile asked for.
#
# `next typegen` must run before the build: Next.js 16 generates the global route types
# (`PageProps`, `LayoutProps`) that the app code depends on.
RUN node_modules/.bin/next typegen && node_modules/.bin/next build

FROM node:24-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The standalone server does not copy `public` or `.next/static` itself — they are expected to be
# served by a CDN. There is no CDN here, so they are copied in and `server.js` serves them.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

RUN useradd --create-home --uid 10001 chessmark && chown -R chessmark:chessmark /app
USER chessmark

EXPOSE 3000
CMD ["node", "server.js"]

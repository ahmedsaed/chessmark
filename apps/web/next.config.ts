import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * Traces the files each route actually needs and copies them, with a minimal `server.js`, into
   * `.next/standalone`. The container then runs without `node_modules` at all — which is the
   * difference between shipping the built app and shipping the whole toolchain that built it.
   */
  output: "standalone",

  /**
   * `/leaderboard/{slug}?q={precision}` was a page: one contestant's rating and the ratable games
   * behind it. `/models/{slug}` is now the only page about a model and carries that as a block per
   * precision, anchored so the `?q=` still lands on what it named.
   *
   * **Here rather than in a `redirect()` from the route**, because `app/leaderboard/loading.tsx`
   * covers the segment and everything under it: a streamed response commits its status line before
   * the page body runs, so a redirect issued from the page would have been served as a `200` with
   * the navigation happening client-side. That is the same trap that turned this project's 404s
   * into 200s ([FRONTEND.md](../../docs/FRONTEND.md#the-404-trap-which-is-still-live)) — a crawler or
   * a link checker would never see the `308`. A config redirect runs before routing, so it does.
   *
   * The slug is one segment, not two: it is published percent-encoded (`vendor%2Fmodel`) because
   * an OpenRouter id contains a slash.
   *
   * **`opengraph-image` is excluded, and the reason is the sentence above.** A config redirect runs
   * before routing, so `/leaderboard/:slug` matched `/leaderboard/opengraph-image` and served the
   * *models* card for the leaderboard — a redirect written for contestant URLs quietly claiming a
   * sibling route added two years later. It is invisible from the page, which still carries the
   * right `og:image` URL; only fetching that URL without following redirects shows it, which is
   * what `site.spec.ts` now does.
   */
  async redirects() {
    // Anything the App Router owns under this segment. A name here is a route that must survive
    // the redirect below, not a slug it should rewrite.
    const notARoute = "((?!opengraph-image$).*)";

    return [
      {
        source: `/leaderboard/:slug${notARoute}`,
        has: [{ type: "query", key: "q", value: "(?<precision>.*)" }],
        destination: "/models/:slug#c-:precision",
        permanent: true,
      },
      {
        source: `/leaderboard/:slug${notARoute}`,
        destination: "/models/:slug",
        permanent: true,
      },
    ];
  },
};

export default nextConfig;

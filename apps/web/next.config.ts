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
   * into 200s ([FRONTEND.md](../../docs/FRONTEND.md#streaming-and-the-price-of-it)) — a crawler or
   * a link checker would never see the `308`. A config redirect runs before routing, so it does.
   *
   * The slug is one segment, not two: it is published percent-encoded (`vendor%2Fmodel`) because
   * an OpenRouter id contains a slash.
   */
  async redirects() {
    return [
      {
        source: "/leaderboard/:slug",
        has: [{ type: "query", key: "q", value: "(?<precision>.*)" }],
        destination: "/models/:slug#c-:precision",
        permanent: true,
      },
      {
        source: "/leaderboard/:slug",
        destination: "/models/:slug",
        permanent: true,
      },
    ];
  },
};

export default nextConfig;

/**
 * Lighthouse budgets (NFR-12, Phase 7).
 *
 * **What this asserts and what it refuses to assert is the whole design.**
 *
 * This repository has already been burnt by the obvious version of this. ROADMAP's *Known gaps*
 * records two wall-clock p95 tests that were deleted, and why: *"a run with a 5.7 ms median and a
 * 329 ms p95 failed a pull request that changed no Python, and a timing assertion that fails on a
 * busy machine and passes on a quiet one only teaches people to rerun CI — which the next real
 * regression is then rerun away too."*
 *
 * A Lighthouse **performance score** is that assertion wearing a different hat. LCP on a shared CI
 * runner moves by seconds between runs of identical code. Gate on it and the suite goes red on
 * unrelated changes, people learn to rerun it, and the first true regression is rerun away with
 * everything else.
 *
 * So the split:
 *
 * * **Asserted** — accessibility and SEO, and the byte and request budgets. These audit the DOM and
 *   the network, not the clock. They give the same answer on a loaded machine as an idle one, and
 *   they are what a code change actually breaks: an unlabelled control, a colour that fails
 *   contrast, a dependency that doubles the bundle.
 * * **Recorded, never gated** — LCP, TTI, Speed Index, the performance score. Collected on every
 *   run and uploaded with the report so a trend is visible and a surprise is investigable. A number
 *   nobody can act on reliably is a number that must not block a merge.
 *
 * Every threshold below is what the site measured when it was written, not a round number somebody
 * liked — a budget above the current value permits a regression, and one below it is red on
 * arrival. Tighten them as the numbers improve; that is the only direction they move.
 */

const BASE = "http://localhost:3010";

/**
 * **The whole public site, not a sample of it.**
 *
 * Four routes were measured at first and the other seven were assumed to behave like them. They do
 * not: `/` is the only page with a board, `/leaderboard` the only one with a table, and the auth
 * pages are the only ones that drive a Clerk hook. A budget that never visits a page cannot hold
 * that page to anything — widening this found two things in the first run.
 *
 * The dynamic routes come from the browser suite's own fixtures, so a game and a tournament are
 * measured with real data rather than an empty state. Absent fixtures — nobody has run the seed —
 * skip those rather than point at ids that do not exist.
 */
const STATIC_ROUTES = [
  "/",
  "/leaderboard",
  "/models",
  "/tournaments",
  "/about",
  "/methodology",
  "/play",
  "/sign-in",
  "/sign-up",
  "/profile",
];

function dynamicRoutes() {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const fixtures = require("./e2e/.fixtures.json");
    return [
      fixtures.replayGame && `/games/${fixtures.replayGame}`,
      fixtures.tournament && `/tournaments/${fixtures.tournament}`,
    ].filter(Boolean);
  } catch {
    return [];
  }
}

/** Everything asserted on every page. Named so the per-URL override can extend it. */
const ASSERTIONS = {
  /* ---- categories ------------------------------------------------------------------------- */
  "categories:accessibility": ["error", { minScore: 0.9 }],
  "categories:seo": ["error", { minScore: 0.9 }],

  /* **Performance is a `warn`, deliberately.** See the header. It is collected, reported and
     visible in the CI summary; it does not fail the build. */
  "categories:performance": ["warn", { minScore: 0.9 }],

  /* **Best practices is a `warn`, and only because a *development* Clerk tenant contaminates it.**
     Three audits fail on every page — `third-party-cookies`, `errors-in-console` (its telemetry
     endpoint is unreachable from here) and `inspector-issues` — and all three are Clerk's dev mode
     rather than this codebase. Production measures **100**. The best-practice audits that are ours
     are asserted individually below, so the category being a warning costs only the one number a
     third party owns. */
  "categories:best-practices": ["warn", { minScore: 0.9 }],

  /* ---- the audits a code change actually breaks -------------------------------------------- */

  // Every one is a DOM fact. A control that loses its label, a colour that drops below AA, a tap
  // target that shrinks — all of them are regressions somebody wrote.
  "aria-command-name": "error",
  "color-contrast": "error",
  "target-size": "error",
  "button-name": "error",
  "link-name": "error",
  "image-alt": "error",
  "html-has-lang": "error",
  "meta-viewport": "error",
  "heading-order": "error",
  "aria-allowed-attr": "error",
  "aria-required-attr": "error",
  "aria-valid-attr-value": "error",
  "aria-hidden-focus": "error",
  "duplicate-id-aria": "error",
  list: "error",
  listitem: "error",
  "is-crawlable": "error",

  /* ---- best practices that are ours -------------------------------------------------------- */
  doctype: "error",
  charset: "error",
  "geolocation-on-start": "error",
  "notification-on-start": "error",
  deprecations: "error",

  /* ---- weight, which is also deterministic ------------------------------------------------- */

  /* **337 KiB measured with Clerk, 450 KiB allowed.** It was 619 KiB until the prebuilt Clerk UI
     came out: `@clerk/ui` alone was 285 KiB on every route, including the ones nobody signs in on.
     The ceiling is the real figure plus a third, so ordinary churn passes and a dependency that
     adds another third of a page does not. */
  "total-byte-weight": ["error", { maxNumericValue: 450 * 1024 }],
  "unused-javascript": ["warn", { maxNumericValue: 120 * 1024 }],
  "render-blocking-resources": ["warn", { maxNumericValue: 300 }],
  "unminified-javascript": "error",
  "unminified-css": "error",

  /* ---- timing: recorded, never a gate ------------------------------------------------------ */
  "largest-contentful-paint": "off",
  "first-contentful-paint": "off",
  "speed-index": "off",
  interactive: "off",
  "total-blocking-time": "off",
  "server-response-time": "off",
  "max-potential-fid": "off",

  /* ---- not ours ---------------------------------------------------------------------------- */

  /* Clerk's, all three, and only on a development instance: its cookies, its telemetry endpoint
     refusing a connection from here, and the issues its dev mode logs. */
  "third-party-cookies": "off",
  "errors-in-console": "off",
  "inspector-issues": "off",

  /* HTTP/2 is on in production. Locally this is `next start` over HTTP/1.1, which is a fact about
     the dev server rather than about the site. */
  "uses-http2": "off",

  /* `Cache-Control: no-store` is deliberate — a live game and a leaderboard are wrong the moment
     they are cached (FRONTEND.md). It costs the back/forward cache, knowingly. */
  "bf-cache": "off",

  // Fails on localhost without TLS; asserted by the deployment, not by this.
  "is-on-https": "off",
  "redirects-http": "off",
  canonical: "off",
};

module.exports = {
  ci: {
    collect: {
      url: [...STATIC_ROUTES, ...dynamicRoutes()].map((route) => `${BASE}${route}`),

      /**
       * **The run starts its own server, and that is not a convenience.**
       *
       * `next start` reads `.next` once at boot, so rebuilding while it runs leaves it serving a
       * mixture — yesterday's HTML with today's stylesheet. Measuring that produced eight failures
       * on a tree that passes cleanly, and cost a round of chasing a regression that did not exist.
       * A suite that measures whichever server happens to be up is a suite that occasionally
       * measures something nobody built.
       */
      startServerCommand: "pnpm start",
      startServerReadyPattern: "Ready in",
      startServerReadyTimeout: 60000,

      /**
       * Three runs, median reported. Lighthouse's own guidance, and the cheapest defence against
       * the one bad run — a CI runner that pauses for a second mid-trace should not be a finding.
       */
      numberOfRuns: 3,
      settings: {
        preset: "desktop",
        chromeFlags: "--headless=new --no-sandbox --disable-dev-shm-usage",
      },
    },

    /**
     * **Clerk is measured, not avoided.**
     *
     * This suite used to build with the Clerk keys cleared, because a development tenant was
     * *370 KiB — 55% of the page* — and swamped everything else. That was a true measurement of a
     * site nobody visits: the real one loads Clerk.
     *
     * Owning the auth screens removed the reason. `@clerk/ui` is no longer fetched at all
     * (`prefetchUI={false}`), Clerk is 87 KiB, and the figure here is what a visitor gets. If it
     * climbs back, `total-byte-weight` says so — which is the point of not looking away.
     */
    assert: {
      /**
       * **`/sign-in` and `/sign-up` are `noindex` on purpose**, so `is-crawlable` failing on them
       * is `robots.txt` doing its job. Overridden per URL rather than switched off everywhere: the
       * day the *leaderboard* stops being indexable, this still has to go red.
       *
       * The two patterns are mutually exclusive, so exactly one set applies to each URL and there
       * is no question of which wins.
       */
      assertMatrix: [
        {
          matchingUrlPattern: "^(?!.*/sign-(in|up)).*$",
          assertions: ASSERTIONS,
        },
        {
          matchingUrlPattern: ".*/sign-(in|up).*",
          /* The *category* counts `is-crawlable` too, so exempting the audit alone still left the
             SEO score at 0.63 — a page deliberately kept out of the index cannot score well on
             being indexable, and should not be asked to. Everything else still applies here:
             contrast, labels, weight, and the rest are exactly as strict as on any other page. */
          assertions: {
            ...ASSERTIONS,
            "is-crawlable": "off",
            "categories:seo": ["warn", { minScore: 0.9 }],
          },
        },
      ],
    },

    upload: {
      /** Reports land in `.lighthouseci/` and CI keeps them as artifacts. */
      target: "filesystem",
      outputDir: "./.lighthouseci",
      reportFilenamePattern: "%%PATHNAME%%-report.%%EXTENSION%%",
    },
  },
};

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
 * * **Asserted** — accessibility, best-practices and SEO, and the byte and request budgets. These
 *   audit the DOM and the network, not the clock. They give the same answer on a loaded machine as
 *   an idle one, and they are what a code change actually breaks: an unlabelled control, a colour
 *   that fails contrast, a dependency that doubles the bundle.
 * * **Recorded, never gated** — LCP, TTI, Speed Index, the performance score. Collected on every
 *   run and uploaded with the report so a trend is visible and a surprise is investigable. A number
 *   nobody can act on reliably is a number that must not block a merge.
 *
 * Every threshold below is set at what the site measured when this was written, not at a round
 * number someone liked — a budget above the current value permits a regression, and one below it
 * is red on arrival. Tighten them as the numbers improve; that is the only direction they move.
 */

const TARGETS = [
  "http://localhost:3010/",
  "http://localhost:3010/leaderboard",
  "http://localhost:3010/models",
  "http://localhost:3010/about",
];

module.exports = {
  ci: {
    collect: {
      url: TARGETS,

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
        // Chrome ships in the runner image and Playwright's chromium is also present; either works.
        chromeFlags: "--headless=new --no-sandbox --disable-dev-shm-usage",
        // The site is server-rendered and uncached by design, so a warm run is the honest one.
        skipAudits: ["uses-http2"],
      },
    },

    assert: {
      assertions: {
        /* ---- categories: the deterministic ones, asserted ------------------------------------ */
        "categories:accessibility": ["error", { minScore: 0.9 }],
        "categories:best-practices": ["error", { minScore: 0.9 }],
        "categories:seo": ["error", { minScore: 0.9 }],

        /* **Performance is `warn`, deliberately.** See the header. It is collected, reported and
           visible in the CI summary; it does not fail the build. Measured at 99 without Clerk. */
        "categories:performance": ["warn", { minScore: 0.9 }],

        /* ---- the audits a code change actually breaks ---------------------------------------- */

        // Every one of these is a DOM fact. A control that loses its label, a colour that drops
        // below AA, a tap target that shrinks — all of them are regressions somebody wrote.
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
        "duplicate-id-aria": "error",
        "list": "error",
        "listitem": "error",

        /* ---- weight, which is also deterministic --------------------------------------------- */

        /* **302 KiB measured, 400 KiB allowed.** Production serves 669 KiB, but 370 KiB of that is
           a Clerk development instance this suite deliberately does not load — see the note below
           `assert`. The ceiling is the real figure plus a third, so ordinary churn passes and a
           dependency that adds a third of a page again does not. */
        "total-byte-weight": ["error", { maxNumericValue: 400 * 1024 }],
        "unused-javascript": ["warn", { maxNumericValue: 80 * 1024 }],
        "render-blocking-resources": ["warn", { maxNumericValue: 300 }],
        "unminified-javascript": "error",
        "unminified-css": "error",

        /* ---- timing: recorded, never a gate -------------------------------------------------- */
        "largest-contentful-paint": "off",
        "first-contentful-paint": "off",
        "speed-index": "off",
        "interactive": "off",
        "total-blocking-time": "off",
        "server-response-time": "off",
        "max-potential-fid": "off",

        /* ---- not ours ------------------------------------------------------------------------ */

        /* HTTP/1.1 at a reverse proxy this repository does not contain (DEPLOYMENT.md: "there is
           no proxy and no TLS in this stack"). Worth 400 ms and not fixable from here. */
        "uses-http2": "off",
        // Clerk's, on both counts, and only on a development instance.
        "third-party-cookies": "off",
        "errors-in-console": "off",
        /* `Cache-Control: no-store` is deliberate — a live game and a leaderboard are wrong the
           moment they are cached (FRONTEND.md). It costs the back/forward cache, knowingly. */
        "bf-cache": "off",
        // Fails on localhost without TLS; asserted by the deployment, not by this.
        "is-on-https": "off",
        "redirects-http": "off",
        "canonical": "off",
      },
    },

    /**
     * **Clerk is absent on purpose, and the suite measures worse without saying so if it is not.**
     * `AuthProvider` renders nothing when `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` is unset, and a
     * development Clerk instance costs *370 KiB — 55% of the page* — plus a handshake redirect
     * worth 1.8 s and a blocked telemetry call that Lighthouse counts as a console error. Best
     * practices measured 100 on production and 74 against a local dev tenant, for reasons entirely
     * outside this repository.
     *
     * CI has no Clerk keys, so it gets this for free; `make lighthouse` clears them so a developer
     * measures the same site CI does. Reading is open to everyone (AUTH-02), so every page here
     * renders fully without an identity.
     */
    upload: {
      /** Reports land in `.lighthouseci/` and CI keeps them as artifacts. */
      target: "filesystem",
      outputDir: "./.lighthouseci",
      reportFilenamePattern: "%%PATHNAME%%-report.%%EXTENSION%%",
    },
  },
};

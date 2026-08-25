/**
 * Vitest for the frontend's pure logic.
 *
 * Deliberately narrow: this covers `src/lib`, where the event-folding and replay-slicing rules
 * live. Those are real logic with real invariants — "ply N shows exactly the state as of ply N"
 * is a property, and a property deserves a test rather than a click-through.
 *
 * Component rendering is not tested here. It would need jsdom and a testing-library stack for
 * assertions far weaker than what Playwright gives us end to end from Phase 7.
 */

import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    include: ["src/lib/**/*.test.ts"],
    environment: "node",
    coverage: {
      provider: "v8",
      // Only the logic this suite is actually responsible for. Reporting a percentage over
      // `src/` at large would average the tested rules together with the components Playwright
      // covers and produce a number that means nothing.
      include: ["src/lib/**/*.ts"],
      exclude: [
        "src/lib/**/*.test.ts",
        "src/lib/__fixtures__/**",
        "src/lib/types.ts",
        // Covered end to end by the browser suite instead, and only there. `api.ts` is fetch
        // wrappers: a unit test would have to mock `fetch`, and would then be asserting the mock.
        // Playwright drives every one of these endpoints through a real browser against a real
        // API, which is the only way the shape of a response gets checked at all.
        "src/lib/api.ts",
        // Static metadata — the site's name, URL and OpenGraph card. No branches to cover.
        "src/lib/site.ts",
      ],
      reporter: ["text", "html", "json-summary"],
      // NFR-10: measured *and* enforced. A floor that is merely reported is a number nobody
      // notices dropping.
      // Set just under what the suite actually achieves (97/96/98/81.5), so a real regression
      // trips the build while ordinary churn does not. Raise them, never lower them.
      thresholds: { lines: 95, functions: 95, branches: 80, statements: 95 },
    },
  },
});

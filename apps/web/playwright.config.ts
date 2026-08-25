import { defineConfig, devices } from "@playwright/test";

import { loadEnv } from "./e2e/env";

loadEnv();

/**
 * The browser suite (Phase 23, NFR-11).
 *
 * Everything Phases 7, 8, 10, 18 and 19 asserted **by hand** until now. No test would have caught
 * a layout regression, and the duplicate-key bug in the conversation panel reached a person
 * playing a real game before anything noticed.
 *
 * Two projects, because the flows differ in what they need:
 *
 * - `public` needs nothing but a running stack. Reading is open to everyone (AUTH-02), so the
 *   lobby, a model page, the leaderboard and a whole replay can be asserted with no identity at
 *   all. This is what CI runs.
 * - `signed-in` needs a real Clerk development instance. It is skipped — loudly, in the report —
 *   when the keys are absent, the same bargain the `llm` pytest marker strikes: a test that
 *   depends on something external is opt-in and never silently green.
 *
 * Nothing here spends money. Model turns are played by `scripts/worker.py --scripted`, which
 * swaps only the provider and leaves the real queue, worker, costing and persistence in place.
 */

const WEB = process.env.E2E_WEB_URL ?? "http://localhost:3010";

export const clerkConfigured = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY,
);

/** Where the signed-in session is parked between the auth project and the tests that need it. */
export const STORAGE_STATE = "e2e/.auth/user.json";

export default defineConfig({
  testDir: "./e2e",
  // A game is driven by a worker in another process; a move is not instant.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  // The suite shares one database. Parallel workers would seed and resign over each other.
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],

  // A scripted turn worker, for the life of the suite. Model turns are played by a worker in
  // another process; without one a human game never gets a reply.
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",

  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  projects: [
    { name: "seed", testMatch: /seed\.setup\.ts/ },
    { name: "auth", testMatch: /auth\.setup\.ts/, dependencies: ["seed"] },
    {
      name: "public",
      testMatch: /public\/.*\.spec\.ts/,
      dependencies: ["seed"],
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "signed-in",
      testMatch: /signed-in\/.*\.spec\.ts/,
      dependencies: ["auth"],
      use: { ...devices["Desktop Chrome"], storageState: STORAGE_STATE },
    },
  ],
});

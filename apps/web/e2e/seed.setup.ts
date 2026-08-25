import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";

import { test as setup, expect } from "@playwright/test";

import { FIXTURES_FILE, type Fixtures } from "./fixtures";

/**
 * Plays a real scripted game into the database and records its id for the suite.
 *
 * A finished game cannot be produced through the browser — replay needs one that already exists —
 * and a hand-written fixture row would assert against shapes this file invented rather than the
 * shapes the runtime writes. `scripts/seed_e2e.py` therefore plays one through the real queue and
 * the real worker, with only the provider scripted.
 */
setup("seed the database", async () => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");

  const stdout = execFileSync("uv", ["run", "python", "../../scripts/seed_e2e.py"], {
    cwd: path.join(repoRoot, "apps", "api"),
    encoding: "utf8",
    env: { ...process.env, PATH: `${process.env.HOME}/.local/bin:${process.env.PATH}` },
  });

  const fixtures = JSON.parse(stdout) as Fixtures;

  // The replay assertions below count on Scholar's Mate specifically: seven plies, White mates.
  // If the seed ever changes shape, fail here rather than in a test whose message would blame
  // the scrubber.
  expect(fixtures.plyCount, "the seeded game should be Scholar's Mate").toBe(7);
  expect(fixtures.result).toBe("1-0");

  writeFileSync(FIXTURES_FILE, JSON.stringify(fixtures, null, 2));
});

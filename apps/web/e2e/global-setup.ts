import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, openSync, writeFileSync } from "node:fs";
import path from "node:path";

import { FIXTURES_FILE, type Fixtures } from "./fixtures";
import { WORKER_PID_FILE, repoRoot } from "./paths";

/**
 * Seeds the database, then starts a turn worker for the life of the suite.
 *
 * **The order is load-bearing.** Both steps consume the same Redis stream, and a job goes to
 * whichever worker reaches it first. Seeding used to run as a Playwright project — that is, after
 * this file had already started the background worker — so the two competed for the seeded game's
 * turns: the seed plays a fixed script (Scholar's Mate) while the worker plays whatever
 * `responsive` decides, and the resulting game was whichever of them happened to win each turn.
 * Locally the seed usually won and the game came out at seven plies; in CI it did not, and the
 * game ran to fifteen. Seeding to completion before the worker exists removes the race rather
 * than making it less likely.
 */
export default async function globalSetup(): Promise<void> {
  const logDir = path.join(__dirname, ".auth");
  mkdirSync(logDir, { recursive: true });

  seed();
  startWorker(logDir);

  // The worker joins the consumer group at start-up. A test that enqueues before it is listening
  // would still be served — jobs are durable (ADR-0007) — but it would wait out the poll, so this
  // just keeps the first test honest about what it is timing.
  await new Promise((resolve) => setTimeout(resolve, 2500));
}

/**
 * Plays a real scripted game into the database and records its id for the suite.
 *
 * A finished game cannot be produced through the browser — replay needs one that already exists —
 * and a hand-written fixture row would assert against shapes this file invented rather than the
 * shapes the runtime writes. `scripts/seed_e2e.py` therefore plays one through the real queue and
 * the real worker, with only the provider scripted.
 */
function seed(): void {
  const stdout = execFileSync("uv", ["run", "python", "../../scripts/seed_e2e.py"], {
    cwd: path.join(repoRoot(), "apps", "api"),
    encoding: "utf8",
    env: { ...process.env, PATH: `${process.env.HOME}/.local/bin:${process.env.PATH}` },
  });

  const fixtures = JSON.parse(stdout) as Fixtures;

  // The replay assertions count on Scholar's Mate specifically: seven plies, White mates. Fail
  // here rather than in a test whose message would blame the scrubber.
  if (fixtures.plyCount !== 7 || fixtures.result !== "1-0") {
    throw new Error(
      `the seeded game should be Scholar's Mate (7 plies, 1-0), got ${fixtures.plyCount} plies ` +
        `and ${fixtures.result}. Another worker consuming the same queue is the usual cause — ` +
        `check that nothing else is running \`make worker\`.`,
    );
  }

  writeFileSync(FIXTURES_FILE, JSON.stringify(fixtures, null, 2));
}

/**
 * A turn worker for the duration of the suite — scripted, so it spends nothing.
 *
 * Model turns in a human-vs-model game are played by a worker in another process. Without one the
 * board simply never moves and every signed-in test times out with a message about a locator,
 * blaming the UI for an absent daemon.
 *
 * `--scripted` swaps only the provider (`agents.scripted.responsive`, which reads the legal moves
 * and plays one). The queue, the turn loop, costing and persistence are the real ones.
 */
function startWorker(logDir: string): void {
  // Its output goes to a file rather than to `ignore`. A worker that fails to start is otherwise
  // completely silent, and every signed-in test then fails on a locator — blaming the UI for an
  // absent daemon. The log is the first place to look when the board never moves.
  const log = openSync(path.join(logDir, "worker.log"), "a");

  const child = spawn("uv", ["run", "python", "../../scripts/worker.py", "--scripted"], {
    cwd: path.join(repoRoot(), "apps", "api"),
    env: { ...process.env, PATH: `${process.env.HOME}/.local/bin:${process.env.PATH}` },
    detached: true,
    stdio: ["ignore", log, log],
  });
  child.unref();

  if (!child.pid) throw new Error("could not start the scripted worker");
  writeFileSync(WORKER_PID_FILE, String(child.pid));
}

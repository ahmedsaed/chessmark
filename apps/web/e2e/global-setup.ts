import { spawn } from "node:child_process";
import { mkdirSync, openSync, writeFileSync } from "node:fs";
import path from "node:path";

import { WORKER_PID_FILE, repoRoot } from "./paths";

/**
 * Starts a turn worker for the duration of the suite — scripted, so it spends nothing.
 *
 * Model turns in a human-vs-model game are played by a worker in another process. Without one the
 * board simply never moves and every signed-in test times out with a message about a locator,
 * blaming the UI for an absent daemon.
 *
 * `--scripted` swaps only the provider (`agents.scripted.responsive`, which reads the legal moves
 * and plays one). The queue, the turn loop, costing and persistence are the real ones, so a game
 * played here exercises the same code a paid game does.
 */
export default async function globalSetup(): Promise<void> {
  const logDir = path.join(__dirname, ".auth");
  mkdirSync(logDir, { recursive: true });

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

  // The worker joins the consumer group at start-up. A test that enqueues before it is listening
  // would still be served — jobs are durable (ADR-0007) — but it would wait out the poll, so this
  // just keeps the first test honest about what it is timing.
  await new Promise((resolve) => setTimeout(resolve, 2500));
}

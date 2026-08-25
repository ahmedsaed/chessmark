import { existsSync, readFileSync, unlinkSync } from "node:fs";

import { WORKER_PID_FILE } from "./paths";

/** Stops the worker the suite started. Leaving one running would consume a person's next game. */
export default async function globalTeardown(): Promise<void> {
  if (!existsSync(WORKER_PID_FILE)) return;

  const pid = Number(readFileSync(WORKER_PID_FILE, "utf8"));
  unlinkSync(WORKER_PID_FILE);

  try {
    // The worker was spawned detached, so it leads its own process group; the negative pid stops
    // `uv` and the Python child it execs, not just the wrapper.
    process.kill(-pid, "SIGTERM");
  } catch {
    // Already gone. Nothing to do, and a teardown that throws hides the real test failure.
  }
}

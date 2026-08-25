import path from "node:path";

export function repoRoot(): string {
  return path.resolve(__dirname, "..", "..", "..");
}

export const WORKER_PID_FILE = path.join(__dirname, ".auth", "worker.pid");

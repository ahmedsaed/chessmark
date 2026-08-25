import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * Loads `.env` files into `process.env` for the suite.
 *
 * Next.js loads `.env.local` for the *app*; Playwright is a separate process and gets nothing.
 * Without this the signed-in project would skip itself on a machine that is fully configured,
 * which is the most confusing possible failure — a green run that tested half of what it claimed.
 *
 * A tiny parser rather than a dependency: these files hold `KEY=value` and comments, and nothing
 * here needs variable expansion or multiline values.
 */
export function loadEnv(): void {
  const web = path.resolve(__dirname, "..");
  const repoRoot = path.resolve(web, "..", "..");

  // Later files must not clobber earlier ones: `.env.local` is the more specific of the two.
  for (const file of [path.join(web, ".env.local"), path.join(repoRoot, ".env")]) {
    if (!existsSync(file)) continue;

    for (const line of readFileSync(file, "utf8").split("\n")) {
      const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
      if (!match) continue;

      const [, key, rawValue] = match;
      if (process.env[key] !== undefined) continue;

      process.env[key] = rawValue.trim().replace(/^["'](.*)["']$/, "$1");
    }
  }
}

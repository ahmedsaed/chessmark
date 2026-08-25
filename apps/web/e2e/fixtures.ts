import { readFileSync } from "node:fs";
import path from "node:path";

/** Where the seed project records what it made. */
export const FIXTURES_FILE = path.join(__dirname, ".fixtures.json");

export interface Fixtures {
  replayGame: string;
  result: string;
  plyCount: number;
}

/** The ids written by the seed project. Read per call so a test never holds a stale one. */
export function fixtures(): Fixtures {
  return JSON.parse(readFileSync(FIXTURES_FILE, "utf8")) as Fixtures;
}

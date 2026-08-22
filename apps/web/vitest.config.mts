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
  },
});

import { describe, expect, it } from "vitest";

import { chooseHero, moveLabel } from "@/lib/featured";
import type { Featured, GameSummary } from "@/lib/types";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const summary = (id: string, status = "finished") => ({ id, status }) as GameSummary;
const pick: Featured = {
  game_id: "picked",
  headline: "The next move was Qg8#.",
  white_quote: null,
  black_quote: null,
  featured_at: "2026-10-20T12:00:00Z",
};

describe("chooseHero", () => {
  it("shows a live game over the pick, and does not dress it as featured", () => {
    expect(chooseHero([summary("live", "running")], pick, [summary("recent")])).toEqual({
      id: "live",
      featured: null,
      settled: false,
    });
  });

  it("shows the pick when nothing is live", () => {
    expect(chooseHero([], pick, [summary("recent")])).toEqual({
      id: "picked",
      featured: pick,
      settled: true,
    });
  });

  it("falls back to the most recent finished game when there is no pick", () => {
    expect(chooseHero([], null, [summary("recent")])).toEqual({
      id: "recent",
      featured: null,
      settled: true,
    });
    expect(chooseHero([], null, [summary("stopped", "aborted")])?.settled).toBe(false);
  });

  it("has nothing to show on an empty site", () => {
    expect(chooseHero([], null, [])).toBeNull();
  });
});

describe("moveLabel", () => {
  it("numbers White's ply with a dot and Black's with an ellipsis", () => {
    expect(moveLabel(63, "Qg8#", START)).toBe("32. Qg8#");
    expect(moveLabel(62, "Qd8", START)).toBe("31… Qd8");
    expect(moveLabel(1, "e4", START)).toBe("1. e4");
  });

  it("counts from the game's own start when Black moves first", () => {
    const blackFirst = "8/8/8/8/8/8/8/K6k b - - 0 1";
    expect(moveLabel(1, "Kg2", blackFirst)).toBe("1… Kg2");
    expect(moveLabel(2, "Kb2", blackFirst)).toBe("2. Kb2");
  });
});

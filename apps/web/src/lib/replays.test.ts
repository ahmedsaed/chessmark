import { describe, expect, it } from "vitest";

import { isCleanFinish, pickReplays } from "@/lib/replays";
import type { GameSummary } from "@/lib/types";

function game(overrides: Partial<GameSummary> & { id: string }): GameSummary {
  return {
    status: "finished",
    result: "1-0",
    termination: "checkmate",
    winner_colour: "white",
    ply_count: 40,
    is_ranked: true,
    trash_talk_enabled: false,
    total_cost_usd: "0.1",
    total_tokens: 100,
    created_at: "2026-08-01T00:00:00Z",
    started_at: "2026-08-01T00:00:00Z",
    ended_at: "2026-08-01T00:10:00Z",
    players: [
      { colour: "white", model: "google/gemini-3.7-flash", forfeited: false },
      { colour: "black", model: "z-ai/glm-4.7", forfeited: false },
    ] as GameSummary["players"],
    ...overrides,
  } as GameSummary;
}

describe("isCleanFinish", () => {
  it("accepts a checkmate and a resignation", () => {
    expect(isCleanFinish(game({ id: "a", termination: "checkmate" }))).toBe(true);
    expect(isCleanFinish(game({ id: "b", termination: "resignation" }))).toBe(true);
  });

  it("rejects the harness stopping a game", () => {
    for (const termination of ["ply_cap", "budget_exceeded", "abandoned", "timeout"]) {
      expect(isCleanFinish(game({ id: termination, termination }))).toBe(false);
    }
  });

  it("rejects a forfeit even when the termination looks ordinary", () => {
    const forfeited = game({
      id: "f",
      termination: "checkmate",
      players: [
        { colour: "white", model: "google/gemini-3.7-flash", forfeited: true },
        { colour: "black", model: "z-ai/glm-4.7", forfeited: false },
      ] as GameSummary["players"],
    });
    expect(isCleanFinish(forfeited)).toBe(false);
  });

  /** The regression: the scripted stub plays a real seven-ply mate with no model behind it. */
  it("rejects a game played by the scripted stub", () => {
    const scripted = game({
      id: "s",
      ply_count: 7,
      players: [
        { colour: "white", model: "scripted/white", forfeited: false },
        { colour: "black", model: "scripted/black", forfeited: false },
      ] as GameSummary["players"],
    });
    expect(isCleanFinish(scripted)).toBe(false);
  });

  it("rejects a game with an unlinked model", () => {
    const orphan = game({
      id: "o",
      players: [
        { colour: "white", model: null, forfeited: false },
        { colour: "black", model: "z-ai/glm-4.7", forfeited: false },
      ] as GameSummary["players"],
    });
    expect(isCleanFinish(orphan)).toBe(false);
  });

  it("rejects a game that is still running", () => {
    expect(isCleanFinish(game({ id: "r", status: "running", termination: null }))).toBe(false);
  });
});

describe("pickReplays", () => {
  const pool = [
    game({ id: "clean-1" }),
    game({ id: "stopped", termination: "ply_cap" }),
    game({ id: "clean-2", termination: "resignation" }),
    game({ id: "clean-3" }),
    game({ id: "running", status: "running", termination: null }),
  ];

  it("returns only clean finishes", () => {
    const picked = pickReplays(pool, 3, () => 0);
    expect(picked.map((g) => g.id).sort()).toEqual(["clean-1", "clean-2", "clean-3"]);
  });

  it("never repeats a game", () => {
    const ids = pickReplays(pool, 3, () => 0.999).map((g) => g.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("returns fewer than asked rather than padding", () => {
    expect(pickReplays([game({ id: "only" })], 3)).toHaveLength(1);
    expect(pickReplays([], 3)).toEqual([]);
  });

  it("does not mutate the caller's array", () => {
    const input = [...pool];
    pickReplays(input, 3, () => 0.5);
    expect(input.map((g) => g.id)).toEqual(pool.map((g) => g.id));
  });

  /** A biased shuffle would leave the pool in its original order for a fixed random. */
  it("actually shuffles", () => {
    const many = Array.from({ length: 12 }, (_, i) => game({ id: `g${i}` }));
    const sequence = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4, 0.5, 0.05, 0.95, 0.15];
    let call = 0;
    const picked = pickReplays(many, 12, () => sequence[call++ % sequence.length]);
    expect(picked.map((g) => g.id)).not.toEqual(many.map((g) => g.id));
  });
});

import { describe, expect, it } from "vitest";

import { gameIsIndexable, MIN_INDEXED_PLIES, modelIsIndexable } from "@/lib/indexing";

describe("gameIsIndexable", () => {
  it("holds back a game shorter than ten plies", () => {
    expect(MIN_INDEXED_PLIES).toBe(10);
    expect(gameIsIndexable({ ply_count: 9 })).toBe(false);
    expect(gameIsIndexable({ ply_count: 0 })).toBe(false);
  });

  it("indexes a game of ten plies or more", () => {
    expect(gameIsIndexable({ ply_count: 10 })).toBe(true);
    expect(gameIsIndexable({ ply_count: 120 })).toBe(true);
  });
});

describe("modelIsIndexable", () => {
  it("holds back a model that has never finished a game", () => {
    expect(modelIsIndexable({ last_played_at: null })).toBe(false);
    /* An API older than the field sends nothing; that must not read as "has played". */
    expect(modelIsIndexable({})).toBe(false);
  });

  it("indexes a model once it has", () => {
    expect(modelIsIndexable({ last_played_at: "2026-10-01T12:00:00Z" })).toBe(true);
  });
});

import { describe, expect, it } from "vitest";

import { orderMyGames, waitingOnYou } from "@/lib/mine";
import type { MyGameSummary } from "@/lib/types";

function game(overrides: Partial<MyGameSummary> & { id: string }): MyGameSummary {
  return {
    status: "running",
    result: "*",
    termination: null,
    winner_colour: null,
    ply_count: 10,
    is_ranked: false,
    trash_talk_enabled: false,
    total_cost_usd: "0.01",
    total_tokens: 100,
    created_at: "2026-08-01T00:00:00Z",
    started_at: "2026-08-01T00:00:00Z",
    ended_at: null,
    players: [] as MyGameSummary["players"],
    your_colour: "white",
    your_turn: false,
    ...overrides,
  } as MyGameSummary;
}

describe("orderMyGames", () => {
  it("puts the games waiting on you first", () => {
    const ordered = orderMyGames([
      game({ id: "theirs" }),
      game({ id: "done", status: "finished" }),
      game({ id: "yours", your_turn: true }),
    ]);

    expect(ordered.map((entry) => entry.id)).toEqual(["yours", "theirs", "done"]);
  });

  it("sinks finished games below every running one", () => {
    const ordered = orderMyGames([
      game({ id: "done", status: "finished" }),
      game({ id: "running" }),
    ]);

    expect(ordered.map((entry) => entry.id)).toEqual(["running", "done"]);
  });

  it("keeps the server's order within a band", () => {
    /* The API returns newest first. Two games both waiting on you must stay in that order rather
       than being reshuffled, so the list does not jump around between loads. */
    const ordered = orderMyGames([
      game({ id: "newer", your_turn: true }),
      game({ id: "older", your_turn: true }),
    ]);

    expect(ordered.map((entry) => entry.id)).toEqual(["newer", "older"]);
  });

  it("does not mutate what it was given", () => {
    const input = [game({ id: "a" }), game({ id: "b", your_turn: true })];
    orderMyGames(input);
    expect(input.map((entry) => entry.id)).toEqual(["a", "b"]);
  });
});

describe("waitingOnYou", () => {
  it("counts only the games where it is your move", () => {
    expect(
      waitingOnYou([
        game({ id: "a", your_turn: true }),
        game({ id: "b" }),
        game({ id: "c", your_turn: true }),
      ]),
    ).toBe(2);
  });

  it("is zero when nothing needs you", () => {
    expect(waitingOnYou([game({ id: "a" })])).toBe(0);
  });
});

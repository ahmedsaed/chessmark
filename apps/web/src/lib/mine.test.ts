import { describe, expect, it } from "vitest";

import { orderMyGames, recordOf, waitingOnYou } from "@/lib/mine";
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

describe("recordOf", () => {
  const decided = (id: string, result: MyGameSummary["result"], seat: "white" | "black") =>
    game({ id, status: "finished", result, your_colour: seat });

  it("reads a win from the caller's own side of the board", () => {
    /* `1-0` is a win for whoever held White and a loss for whoever held Black. Reading the result
       without the seat is how a record ends up being the opponent's. */
    const record = recordOf([decided("a", "1-0", "white"), decided("b", "1-0", "black")]);

    expect(record.wins).toBe(1);
    expect(record.losses).toBe(1);
  });

  it("counts both colours of loss", () => {
    const record = recordOf([decided("a", "0-1", "white"), decided("b", "1-0", "black")]);

    expect(record).toMatchObject({ wins: 0, losses: 2, draws: 0, decided: 2 });
  });

  it("counts a draw for either seat", () => {
    const record = recordOf([
      decided("a", "1/2-1/2", "white"),
      decided("b", "1/2-1/2", "black"),
    ]);

    expect(record).toMatchObject({ draws: 2, wins: 0, losses: 0 });
  });

  it("keeps a running game out of the record entirely", () => {
    /* Not a draw, and not half a game. A record that counted games in progress would move every
       time an opponent thought, and would be wrong in the direction of whatever was unfinished. */
    const record = recordOf([
      game({ id: "a", status: "running" }),
      game({ id: "b", status: "paused" }),
      game({ id: "c", status: "pending" }),
    ]);

    expect(record).toMatchObject({ decided: 0, wins: 0, draws: 0, losses: 0, unfinished: 3 });
  });

  it("counts a harness stop apart from a draw", () => {
    /**
     * **Invariant 11, in arithmetic.** A game finished with no result is a budget, a ply cap or a
     * provider we could not reach — our ceiling, not a finding about a player. `pool-free` round
     * 175 is the case: a 300-ply cap drew a game White was winning outright, and folding that into
     * the draws column would record our own limit as a result somebody played to.
     */
    const record = recordOf([
      game({ id: "a", status: "finished", result: "*" }),
      game({ id: "b", status: "aborted", result: "*" }),
    ]);

    expect(record).toMatchObject({ undecided: 2, draws: 0, decided: 0, unfinished: 0 });
  });

  it("counts an empty history as an empty record rather than dividing by zero", () => {
    expect(recordOf([])).toEqual({
      decided: 0,
      wins: 0,
      draws: 0,
      losses: 0,
      unfinished: 0,
      undecided: 0,
    });
  });

  it("puts every game in exactly one bucket", () => {
    /* The four columns are printed beside "Played", so they have to add up to it — a game counted
       twice or not at all makes the page contradict itself in a way no single assertion above
       would catch. */
    const games = [
      decided("a", "1-0", "white"),
      decided("b", "0-1", "white"),
      decided("c", "1/2-1/2", "black"),
      game({ id: "d", status: "running" }),
      game({ id: "e", status: "finished", result: "*" }),
    ];

    const r = recordOf(games);

    expect(r.wins + r.draws + r.losses + r.unfinished + r.undecided).toBe(games.length);
    expect(r.wins + r.draws + r.losses).toBe(r.decided);
  });
});

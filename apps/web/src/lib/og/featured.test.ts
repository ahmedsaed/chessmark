/**
 * Which game a card draws.
 *
 * The decision, separated from the fetch, because *which* game is the part that can be wrong in a
 * way nobody notices: a card that silently falls back to the opening position looks fine and says
 * nothing, which is what four of these cards did before there was a rule here at all.
 */

import { describe, expect, it } from "vitest";

import { pickGame } from "@/lib/og/featured";
import type { GameSummary } from "@/lib/types";

function game(over: Partial<GameSummary> & { id: string }): GameSummary {
  return {
    status: "finished",
    result: "1-0",
    termination: "checkmate",
    winner_colour: "white",
    ply_count: 40,
    is_ranked: true,
    trash_talk_enabled: false,
    total_cost_usd: "0",
    total_tokens: 0,
    created_at: "2026-09-01T00:00:00Z",
    started_at: "2026-09-01T00:00:00Z",
    ended_at: "2026-09-01T01:00:00Z",
    pause_reason: null,
    resume_after: null,
    players: [],
    ...over,
  };
}

describe("pickGame", () => {
  it("prefers a live game over any finished one", () => {
    /* The site's whole pitch is that these models are playing *now*, so a live board beats a
       better-looking finished one. Same rule the landing page uses to choose its hero. */
    const picked = pickGame([
      game({ id: "old", ended_at: "2026-09-16T00:00:00Z" }),
      game({ id: "live", status: "running", ended_at: null, ply_count: 12 }),
    ]);

    expect(picked?.id).toBe("live");
  });

  it("prefers the live game that is furthest along", () => {
    // A board with pieces developed says more than one three plies in.
    const picked = pickGame([
      game({ id: "just-started", status: "running", ended_at: null, ply_count: 3 }),
      game({ id: "deep", status: "running", ended_at: null, ply_count: 61 }),
    ]);

    expect(picked?.id).toBe("deep");
  });

  it("falls back to the most recently finished game", () => {
    const picked = pickGame([
      game({ id: "older", ended_at: "2026-09-10T00:00:00Z" }),
      game({ id: "newest", ended_at: "2026-09-17T00:00:00Z" }),
      game({ id: "middle", ended_at: "2026-09-14T00:00:00Z" }),
    ]);

    expect(picked?.id).toBe("newest");
  });

  it("ignores a game that never reached a position", () => {
    /* A pairing abandoned at ply 0 has nothing on its board but the opening position, which is the
       thing this function exists to stop a card showing. */
    const picked = pickGame([
      game({ id: "empty", status: "aborted", ply_count: 0, ended_at: "2026-09-17T00:00:00Z" }),
      game({ id: "played", ended_at: "2026-09-10T00:00:00Z" }),
    ]);

    expect(picked?.id).toBe("played");
  });

  it("returns null when there is nothing worth drawing", () => {
    // The caller draws the opening position then — the one case where it is honest, because it is
    // what an empty Chessmark actually looks like.
    expect(pickGame([])).toBeNull();
    expect(pickGame([game({ id: "empty", ply_count: 0 })])).toBeNull();
  });

  it("orders by when a game ran, not by the order the API returned it", () => {
    const picked = pickGame([
      game({ id: "listed-first", ended_at: "2026-01-01T00:00:00Z" }),
      game({ id: "listed-last", ended_at: "2026-09-17T00:00:00Z" }),
    ]);

    expect(picked?.id).toBe("listed-last");
  });

  it("uses the start time for a game that has not ended", () => {
    const picked = pickGame([
      game({ id: "paused-recent", status: "paused", ended_at: null, started_at: "2026-09-17T00:00:00Z" }),
      game({ id: "finished-old", ended_at: "2026-09-01T00:00:00Z" }),
    ]);

    expect(picked?.id).toBe("paused-recent");
  });

  it("does not throw on an unparseable timestamp", () => {
    // Never observed, and this runs inside image generation where a throw is a broken-image box.
    const picked = pickGame([
      game({ id: "broken", ended_at: "not a date" }),
      game({ id: "fine", ended_at: "2026-09-17T00:00:00Z" }),
    ]);

    expect(picked?.id).toBe("fine");
  });
});

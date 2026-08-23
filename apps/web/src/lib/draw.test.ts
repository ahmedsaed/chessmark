import { describe, expect, it } from "vitest";

import { openDrawOffer } from "@/lib/draw";
import type { GameEvent } from "@/lib/types";

function event(type: string, payload: Record<string, unknown>, seq = 1): GameEvent {
  return { seq, type, payload } as GameEvent;
}

describe("openDrawOffer", () => {
  it("is null when nothing was offered", () => {
    expect(openDrawOffer([event("move_made", { ply: 1 })], 1)).toBeNull();
  });

  it("finds the model's standing offer", () => {
    expect(openDrawOffer([event("draw_offered", { ply: 4 }, 9)], 4)).toBe("opponent");
  });

  it("tells your own offer apart from theirs", () => {
    expect(openDrawOffer([event("draw_offered", { ply: 4, human: true }, 9)], 4)).toBe("you");
  });

  /** An offer stands only for the position it was made in. */
  it("lapses once a move has been played", () => {
    const events = [
      event("draw_offered", { ply: 4 }, 9),
      event("move_made", { ply: 5 }, 10),
    ];
    expect(openDrawOffer(events, 5)).toBeNull();
  });

  it("ignores an offer made at an earlier position", () => {
    expect(openDrawOffer([event("draw_offered", { ply: 2 }, 9)], 6)).toBeNull();
  });

  it("takes the most recent offer when several were made", () => {
    const events = [
      event("draw_offered", { ply: 4, human: true }, 8),
      event("draw_offered", { ply: 4 }, 9),
    ];
    expect(openDrawOffer(events, 4)).toBe("opponent");
  });

  it("is null for an empty log", () => {
    expect(openDrawOffer([], 0)).toBeNull();
  });
});

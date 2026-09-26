/**
 * A decision model's turn in the timeline (ADR-0049).
 *
 * A decision seat writes no reasoning and calls no tools, so its turn is `turn_started`, one
 * `decided`, and the move. These pin that the panel draws that answer — and draws a *withheld*
 * answer as withheld rather than as a model that weighed nothing.
 */

import { describe, expect, it } from "vitest";
import { decisionBlock, foldEvents, liveBlocks } from "@/lib/turns";
import type { DecisionBlock, GameEvent, LiveFrame } from "@/lib/types";

const PAYLOAD = {
  player_id: "p1",
  colour: "white",
  ply: 1,
  model: "typesafe/jev-1.13-20260917",
  duration_ms: 812,
  action: "move",
  choice: "e4",
  offers_draw: false,
  options: 20,
  probabilities: [
    ["e4", 0.41],
    ["d4", 0.33],
    ["Nf3", 0.1],
  ],
  confidence: 0.3,
  answers: { resign: 0.01, offer_draw: 0.04 },
};

function decidedTurn(payload: Record<string, unknown>): GameEvent[] {
  return [
    { seq: 1, type: "turn_started", payload: { ply: 1, colour: "white", player_id: "p1" } },
    { seq: 2, type: "decided", payload },
    { seq: 3, type: "move_made", payload: { ply: 1, colour: "white", san: "e4" } },
  ];
}

describe("a decision turn", () => {
  it("is one step carrying the whole answer, and closes on its move", () => {
    const { turns, moves } = foldEvents(decidedTurn(PAYLOAD), []);
    expect(moves).toEqual(["e4"]);
    expect(turns).toHaveLength(1);
    const [block] = turns[0].blocks as DecisionBlock[];
    expect(block).toMatchObject({
      kind: "decision",
      action: "move",
      choice: "e4",
      options: 20,
      offersDraw: false,
      confidence: 0.3,
      durationMs: 812,
    });
    expect(block.probabilities?.[0]).toEqual(["e4", 0.41]);
    expect(block.answers).toEqual({ resign: 0.01, offer_draw: 0.04 });
    expect(turns[0].live).toBe(false);
  });

  it("says a withheld answer is withheld, not that the model weighed nothing", () => {
    // What `api/redaction.py` sends a person playing the game: the action, not the weighing.
    const { probabilities, confidence, answers, ...visible } = PAYLOAD;
    void probabilities;
    void confidence;
    void answers;
    const { turns } = foldEvents(decidedTurn(visible), []);
    const [block] = turns[0].blocks as DecisionBlock[];
    expect(block.choice).toBe("e4");
    expect(block.probabilities).toBeNull();
    expect(block.answers).toBeNull();
    expect(block.confidence).toBeNull();
  });

  it("carries an ending the majority rule overruled, and nothing when it did not", () => {
    const overruled = decisionBlock(
      { ...PAYLOAD, ranked_first: "resign", answers: { resign: 0.45, play_on: 0.38 } },
      2,
    );
    expect(overruled.rankedFirst).toBe("resign");
    expect(decisionBlock(PAYLOAD, 2).rankedFirst).toBeNull();
  });

  it("reads a resignation as what the seat did", () => {
    const block = decisionBlock({ ...PAYLOAD, action: "resign" }, 2);
    expect(block.action).toBe("resign");
  });

  it("draws the live frame exactly as the event that settles it", () => {
    const frame = { frame: "block", kind: "decision", ...PAYLOAD } as unknown as LiveFrame;
    const [live] = liveBlocks([frame]) as DecisionBlock[];
    const committed = decisionBlock(PAYLOAD, 2);
    expect({ ...live, seq: 0 }).toEqual({ ...committed, seq: 0 });
  });

  it("drops a malformed entry rather than drawing a bar for nothing", () => {
    const block = decisionBlock({ ...PAYLOAD, probabilities: [["e4", 0.5], ["bad"], [3, 0.1]] }, 2);
    expect(block.probabilities).toEqual([["e4", 0.5]]);
  });
});

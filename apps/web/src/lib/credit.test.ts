import { describe, expect, it } from "vitest";

import { canPay, formatBalance, gameCost, limitFrom, modelMoveCharged } from "@/lib/credit";

describe("formatBalance", () => {
  it("writes a balance in dollars and cents", () => {
    expect(formatBalance("4.36")).toBe("$4.36");
    expect(formatBalance("0E-8")).toBe("$0.00");
    expect(formatBalance("12.5")).toBe("$12.50");
  });

  it("does not round a balance under a cent to an empty-looking zero", () => {
    expect(formatBalance("0.00420000")).toBe("$0.0042");
  });

  it("says a balance a turn below zero as it is", () => {
    expect(formatBalance("-0.0031")).toBe("−$0.0031");
    expect(formatBalance("-1.5")).toBe("−$1.50");
  });
});

describe("canPay", () => {
  it("is anything above zero, however small", () => {
    expect(canPay("0.0001")).toBe(true);
    expect(canPay("0E-8")).toBe(false);
    expect(canPay("-0.002")).toBe(false);
  });
});

describe("modelMoveCharged", () => {
  const base = { pays: true, before: 4, after: 5, seat: undefined } as const;

  it("is a model's move in a game the viewer pays for", () => {
    expect(modelMoveCharged({ ...base, mover: "white" })).toBe(true);
  });

  it("is not the viewer's own move, which costs nothing", () => {
    expect(modelMoveCharged({ ...base, mover: "white", seat: "white" })).toBe(false);
    expect(modelMoveCharged({ ...base, mover: "black", seat: "white" })).toBe(true);
  });

  it("is nothing in a game somebody else pays for", () => {
    expect(modelMoveCharged({ ...base, pays: false, mover: "white" })).toBe(false);
  });

  it("is nothing without a new ply", () => {
    expect(modelMoveCharged({ ...base, after: 4, mover: "white" })).toBe(false);
  });
});

describe("limitFrom", () => {
  it("is no limit when left empty", () => {
    expect(limitFrom("")).toBeNull();
    expect(limitFrom("   ")).toBeNull();
  });

  it("is dollars and cents when set", () => {
    expect(limitFrom("5")).toBe("5.00");
    expect(limitFrom("2.5")).toBe("2.50");
  });

  it("refuses a limit that would stop the game before it starts", () => {
    expect(limitFrom("0")).toBeNull();
    expect(limitFrom("-3")).toBeNull();
  });
});

describe("gameCost", () => {
  it("is the recorded cost while the game has not been reconciled", () => {
    expect(gameCost({ total_cost_usd: "0.0123", billed_usd: null })).toEqual({
      usd: "0.0123",
      note: null,
    });
  });

  it("is the billed cost once reconciled, and says nothing when the two agree", () => {
    expect(gameCost({ total_cost_usd: "0.00831349", billed_usd: "0.00831349" }).note).toBeNull();
  });

  it("says why when OpenRouter billed more than the game recorded", () => {
    const shown = gameCost({
      total_cost_usd: "0.37833",
      billed_usd: "0.418989",
      billed_requests: 166,
      unrecorded_requests: 21,
    });
    expect(shown.usd).toBe("0.418989");
    expect(shown.note).toContain("$0.42 for 166 requests");
    expect(shown.note).toContain("$0.04 of it is 21 requests");
  });

  it("says so when the bill came in under the record", () => {
    expect(gameCost({ total_cost_usd: "0.003", billed_usd: "0.0025" }).note).toContain("refunded");
  });
});

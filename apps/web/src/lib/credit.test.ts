import { describe, expect, it } from "vitest";

import { canPay, formatBalance, gameCost, limitFrom, modelMoveCharged, usd } from "@/lib/credit";

describe("formatBalance", () => {
  it("writes a balance in dollars and cents", () => {
    expect(formatBalance("4.36")).toBe("$4.36");
    expect(formatBalance("0E-8")).toBe("$0.00");
    expect(formatBalance("12.5")).toBe("$12.50");
  });

  it("does not round a balance under a cent to an empty-looking zero", () => {
    expect(formatBalance("0.00420000")).toBe("$0.0042");
  });

  it("shows a balance that is not whole cents to four places, so spending is visible", () => {
    // A decision game took $1.00 to $0.9962; rounded to cents it still read $1.00.
    expect(formatBalance("0.99615940")).toBe("$0.9962");
    expect(formatBalance("1.00000000")).toBe("$1.00");
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

  it("is the amount as typed when set, even under a cent", () => {
    expect(limitFrom("5")).toBe("5");
    expect(limitFrom(" 2.5 ")).toBe("2.5");
    expect(limitFrom("0.0005")).toBe("0.0005");
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
    expect(shown.note).toContain("$0.4190 for 166 requests");
    expect(shown.note).toContain("$0.0407 of it is 21 requests");
  });

  it("says so when the bill came in under the record", () => {
    expect(gameCost({ total_cost_usd: "0.003", billed_usd: "0.0025" }).note).toContain("refunded");
  });
});

describe("usd", () => {
  it("keeps a game's seats and its total adding up", () => {
    // The seats of a real decision game, and its total: 0.00236 + 0.00148 = 0.00384.
    expect(usd("0.0023604")).toBe("$0.002360");
    expect(usd("0.00148020")).toBe("$0.001480");
    expect(usd("0.00384060")).toBe("$0.003841");
  });

  it("uses four places below a dollar and cents above", () => {
    expect(usd("0.418989")).toBe("$0.4190");
    expect(usd("12.3456")).toBe("$12.35");
  });
});

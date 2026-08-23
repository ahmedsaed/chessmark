import { describe, expect, it } from "vitest";

import { tailMoves } from "@/lib/moves";

describe("tailMoves", () => {
  it("is empty for no moves", () => {
    expect(tailMoves([])).toBe("");
  });

  it("numbers pairs from move one when nothing is trimmed", () => {
    expect(tailMoves(["e4", "c5", "Nf3", "d6"])).toBe("1.e4 c5  2.Nf3 d6");
  });

  it("leaves a lone white move unpaired", () => {
    expect(tailMoves(["e4"])).toBe("1.e4");
    expect(tailMoves(["e4", "c5", "Nf3"])).toBe("1.e4 c5  2.Nf3");
  });

  it("marks a trimmed list with a leading ellipsis", () => {
    const moves = Array.from({ length: 20 }, (_, i) => `m${i}`);
    expect(tailMoves(moves, 4)).toBe("… 9.m16 m17  10.m18 m19");
  });

  it("does not trim when the list is exactly the window", () => {
    expect(tailMoves(["e4", "c5"], 2)).toBe("1.e4 c5");
  });

  /**
   * The off-by-one this function exists to avoid: an odd window would slice mid-pair and print
   * Black's reply under White's move number.
   */
  it("always starts the window on a white move", () => {
    const moves = ["e4", "c5", "Nf3", "d6", "d4", "cxd4"];
    // A window of 3 would start at index 3 (Black's `d6`); it must back up to index 2.
    expect(tailMoves(moves, 3)).toBe("… 2.Nf3 d6  3.d4 cxd4");
  });

  it("keeps numbering aligned with the true ply index after trimming", () => {
    const moves = Array.from({ length: 41 }, (_, i) => `m${i}`);
    // 41 plies, window 12 -> wanted 29 (odd) -> from 28 -> first pair is move 15.
    expect(tailMoves(moves, 12).startsWith("… 15.m28 m29")).toBe(true);
    // The final lone white move keeps its own number.
    expect(tailMoves(moves, 12).endsWith("21.m40")).toBe(true);
  });
});

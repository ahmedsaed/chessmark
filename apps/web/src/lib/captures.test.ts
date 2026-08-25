import { describe, expect, it } from "vitest";

import { captures } from "@/lib/captures";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

describe("captures", () => {
  it("finds nothing taken in the starting position", () => {
    expect(captures(START)).toEqual({ white: [], black: [], advantage: 0 });
  });

  it("counts a piece each side has taken", () => {
    // White is missing a knight, Black a pawn: Black took the knight, White took the pawn.
    const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/R1BQKBNR w KQkq - 0 1";
    const { white, black } = captures("rnbqkbn1/pppppppp/8/8/8/8/PPPPPPPP/R1BQKBNR w - - 0 1");

    expect(black).toEqual(["n"]);
    expect(white).toEqual(["r"]);
    expect(captures(fen).black).toEqual(["n"]);
  });

  it("orders the heaviest piece first", () => {
    /* A row of captures should read queen, rook, bishop, knight, pawn — not the order they
       happened to leave the board in. */
    const fen = "4k3/8/8/8/8/8/PPPPPPPP/4K3 w - - 0 1";
    expect(captures(fen).white).toEqual(["q", "r", "r", "b", "b", "n", "n", "p", "p", "p", "p", "p", "p", "p", "p"]);
  });

  it("computes the advantage from White's point of view", () => {
    // Black is missing a rook: White is up five.
    const fen = "1nbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQk - 0 1";
    expect(captures(fen).advantage).toBe(5);
  });

  it("reports a negative advantage when Black is ahead", () => {
    const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/1NBQKBNR w KQkq - 0 1";
    expect(captures(fen).advantage).toBe(-5);
  });

  it("reports level material when both sides have taken the same", () => {
    const fen = "1nbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/1NBQKBNR w KQk - 0 1";
    const { advantage } = captures(fen);

    expect(advantage).toBe(0);
  });

  it("does not invent a capture when a side has promoted", () => {
    /* A promotion leaves White with two queens. Subtracting from the starting count would give
       -1 and read as Black having captured a queen that never existed. The spent pawn is already
       counted as missing, so the material total stays right. */
    const fen = "4k3/8/8/8/8/8/8/3QQK2 w - - 0 1";
    const { black } = captures(fen);

    expect(black.filter((piece) => piece === "q")).toEqual([]);
    expect(black.filter((piece) => piece === "p")).toHaveLength(8);
  });

  it("ignores kings, which are never captured", () => {
    const fen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1";
    const { white, black } = captures(fen);

    expect(white).not.toContain("k");
    expect(black).not.toContain("k");
    expect(captures(fen).advantage).toBe(0);
  });

  it("reads a bare placement field without the rest of a FEN", () => {
    expect(captures("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR").advantage).toBe(0);
  });
});

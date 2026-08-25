/**
 * Legal destinations for a selected piece.
 *
 * This is the rule behind both the target dots and click-to-move, so the cases that matter are the
 * ones a player would notice being wrong: a piece that cannot move, an opponent's piece, and the
 * captures that need drawing differently.
 */

import { describe, expect, it } from "vitest";

import { legalTargets } from "@/lib/board";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

function squares(fen: string, from: string): string[] {
  return legalTargets(fen, from)
    .map((target) => target.to)
    .sort();
}

describe("legalTargets", () => {
  it("gives a pawn its one and two square advances", () => {
    expect(squares(START, "e2")).toEqual(["e3", "e4"]);
  });

  it("gives a knight its opening moves and not the square its own pawn holds", () => {
    expect(squares(START, "g1")).toEqual(["f3", "h3"]);
  });

  it("returns nothing for a piece hemmed in by its own side", () => {
    expect(legalTargets(START, "c1")).toEqual([]);
  });

  it("returns nothing for an empty square", () => {
    expect(legalTargets(START, "e5")).toEqual([]);
  });

  it("returns nothing for the opponent's piece", () => {
    /* This is the access control, not a nicety: selection is gated on there being somewhere to go,
       so a player must not be able to pick up the piece their opponent is about to move. */
    expect(legalTargets(START, "e7")).toEqual([]);
  });

  it("marks a capture apart from a quiet move", () => {
    // Black pawn on d5, white pawn on e4: exd5 captures, e5 does not.
    const fen = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
    const targets = legalTargets(fen, "e4");

    expect(targets.find((t) => t.to === "d5")?.capture).toBe(true);
    expect(targets.find((t) => t.to === "e5")?.capture).toBe(false);
  });

  it("treats en passant as a capture even though the square is empty", () => {
    /* The one capture whose target square holds no piece. Drawn as a quiet move it would read as
       a push onto an empty square, which is exactly the move it is not. */
    const fen = "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3";
    const targets = legalTargets(fen, "e5");

    expect(targets.find((t) => t.to === "f6")?.capture).toBe(true);
  });

  it("offers one entry per square for a promotion", () => {
    /* chess.js generates four moves to the same square, one per promotion piece. The board always
       promotes to a queen, so four dots would be four copies of one choice. */
    const fen = "8/4P3/8/8/8/8/8/K6k w - - 0 1";
    const targets = legalTargets(fen, "e7");

    expect(targets.filter((t) => t.to === "e8")).toHaveLength(1);
  });

  it("excludes a move that would leave the king in check", () => {
    // The knight on d2 is pinned to the king on e1 by the bishop on b4.
    const fen = "4k3/8/8/8/1b6/8/3N4/4K3 w - - 0 1";
    expect(legalTargets(fen, "d2")).toEqual([]);
  });

  it("answers with no moves rather than throwing on nonsense input", () => {
    /* A board that cannot answer "where can this go" should offer no move, not break the page
       around it. */
    expect(legalTargets(START, "z9")).toEqual([]);
    expect(legalTargets("not a fen", "e2")).toEqual([]);
  });
});

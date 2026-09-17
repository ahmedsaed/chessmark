/**
 * FEN to a board a social card can draw.
 *
 * Worth a unit test rather than an eyeball on a rendered card for one reason: this runs inside
 * image generation, where a thrown exception is not an error page but a *broken-image box beside
 * a link*, on somebody else's timeline, for as long as the card is cached. A card is the one place
 * in the app where failing loudly is worse than failing plainly, so the parser is written to never
 * throw and this is what says so.
 */

import { describe, expect, it } from "vitest";

import { ranks, START_PLACEMENT } from "@/lib/og/fen";

const shape = (fen: string) => ranks(fen).map((rank) => rank.length);

describe("ranks", () => {
  it("lays the opening position out as eight ranks of eight, rank 8 first", () => {
    const board = ranks(START_PLACEMENT);

    expect(shape(START_PLACEMENT)).toEqual([8, 8, 8, 8, 8, 8, 8, 8]);
    // a8 is a black rook, a1 a white one: rank 8 is drawn first, and the colours are what say
    // which end of the board a reader is looking at.
    expect(board[0][0]).toEqual({ glyph: "♜", white: false });
    expect(board[7][0]).toEqual({ glyph: "♜", white: true });
    expect(board[3].every((square) => square === null)).toBe(true);
  });

  it("takes a whole FEN as readily as a bare placement field", () => {
    // A game record carries `current_fen`; a card with no game names the opening position.
    expect(ranks(`${START_PLACEMENT} w KQkq - 0 1`)).toEqual(ranks(START_PLACEMENT));
  });

  it("expands digits into empty squares", () => {
    const [rank] = ranks("4k3/8/8/8/8/8/8/8");

    expect(rank.filter((square) => square !== null)).toEqual([{ glyph: "♚", white: false }]);
    expect(rank).toHaveLength(8);
  });

  it("separates the two colours by case, not by glyph", () => {
    /* Both sides are drawn with the filled glyphs and told apart by `color`. The outline set is
       what a "white" piece nominally is, and a white outline on a light square is invisible. */
    const [rank] = ranks("Qq6/8/8/8/8/8/8/8");

    expect(rank[0]).toEqual({ glyph: "♛", white: true });
    expect(rank[1]).toEqual({ glyph: "♛", white: false });
  });

  it("pads a short rank rather than drawing a ragged board", () => {
    expect(shape("4k/8/8/8/8/8/8/8")).toEqual([8, 8, 8, 8, 8, 8, 8, 8]);
  });

  it("trims a long rank rather than overflowing the card", () => {
    expect(shape("kkkkkkkkkkkk/8/8/8/8/8/8/8")).toEqual([8, 8, 8, 8, 8, 8, 8, 8]);
  });

  it("returns a full board for a FEN with too few ranks", () => {
    expect(shape("8/8/8")).toEqual([8, 8, 8, 8, 8, 8, 8, 8]);
  });

  it("never throws on nonsense", () => {
    // Not hypothetical: the id in the URL is whatever somebody pasted, and the API's answer is
    // whatever the record says. Neither is this function's to validate.
    for (const nonsense of ["", " ", "///////", "xyz", "9999999999", "8/8/8/8/8/8/8/8/8/8/8"]) {
      expect(() => ranks(nonsense)).not.toThrow();
      expect(shape(nonsense)).toEqual([8, 8, 8, 8, 8, 8, 8, 8]);
    }
  });
});

/**
 * The exported PGN, read back by a parser that did not write it.
 *
 * Phase 8 requires the file to open in Lichess and SCID. Neither runs here, so this is the
 * strongest available proxy: `chess.js` is a third implementation of PGN, independent of the
 * `python-chess` that produced the file. A tag or escape that only round-trips through its own
 * writer fails here.
 *
 * The fixture is a real export — the 80-ply gemini-2.5-flash-lite vs deepseek-v4-flash benchmark
 * game, straight from `GET /games/{id}/pgn`. Verification against Lichess and SCID themselves is
 * still owed, and the roadmap says so rather than implying this covers it.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";

const pgn = readFileSync(join(__dirname, "__fixtures__/game.pgn"), "utf8");

describe("the exported PGN", () => {
  it("loads in a parser that is not the one that wrote it", () => {
    const board = new Chess();

    expect(() => board.loadPgn(pgn)).not.toThrow();
  });

  it("replays to the position the game ended in", () => {
    const board = new Chess();
    board.loadPgn(pgn);

    /* Byte-for-byte what `GET /games/{id}` reports as `current_fen`, halfmove clock and move
       number included — derived here by a different engine from only the move text. */
    expect(board.fen()).toBe("5rk1/pp5p/8/5p2/2p5/8/bKn5/4r3 w - - 2 41");
  });

  it("carries every ply", () => {
    const board = new Chess();
    board.loadPgn(pgn);

    expect(board.history()).toHaveLength(80);
    expect(board.history().slice(0, 4)).toEqual(["e4", "e5", "Bc4", "Nc6"]);
  });

  it("keeps the provenance tags a third-party reader can see", () => {
    const board = new Chess();
    board.loadPgn(pgn);
    const headers = board.getHeaders();

    expect(headers.White).toBe("gemini-2.5-flash-lite");
    expect(headers.Black).toBe("deepseek-v4-flash");
    expect(headers.Result).toBe("1/2-1/2");
    expect(headers.ChessmarkPromptVersion).toBe("v1");
    expect(headers.ChessmarkWhiteIllegalAttempts).toBe("4");
  });
});

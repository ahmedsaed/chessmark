import { describe, expect, it } from "vitest";

import { readGif } from "@/lib/__fixtures__/gif";
import { encodeGif } from "@/lib/gif/encode";
import {
  DELAY,
  gifCaptions,
  gifDelays,
  gifFilename,
  gifFrames,
  gifOrientation,
} from "@/lib/gif/frames";
import type { Player } from "@/lib/types";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const SCHOLARS = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"];

const seat = (colour: "white" | "black", kind: Player["kind"], name: string) =>
  ({ colour, kind, display_name: name }) as Player;

describe("gifFrames", () => {
  it("is the opening position and then one frame per ply", () => {
    const frames = gifFrames(START, SCHOLARS);
    expect(frames).toHaveLength(SCHOLARS.length + 1);
    expect(frames[0]).toEqual({ fen: START, lastMove: null });
    expect(frames[1]!.lastMove).toEqual({ from: "e2", to: "e4" });
    expect(frames.at(-1)!.lastMove).toEqual({ from: "h5", to: "f7" });
    expect(frames.at(-1)!.fen.split(" ")[0]).toBe(
      "r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR",
    );
  });

  it("replays from the game's own start, not the standard one", () => {
    // Black to move: `e5` is only legal because the FEN says so.
    const fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
    const frames = gifFrames(fen, ["e5"]);
    expect(frames).toHaveLength(2);
    expect(frames[1]!.lastMove).toEqual({ from: "e7", to: "e5" });
  });

  it("stops at a move that does not replay instead of throwing", () => {
    expect(gifFrames(START, ["e4", "Ke7", "d4"])).toHaveLength(2);
  });

  it("is empty for a start position it cannot read", () => {
    expect(gifFrames("not a fen", ["e4"])).toEqual([]);
  });
});

describe("gifDelays", () => {
  it("holds the opening and, longer, the final position", () => {
    expect(gifDelays(4)).toEqual([DELAY.first, DELAY.ply, DELAY.ply, DELAY.last]);
    expect(DELAY.last).toBeGreaterThan(DELAY.ply);
  });

  it("gives a single frame the final hold", () => {
    expect(gifDelays(1)).toEqual([DELAY.last]);
  });
});

describe("gifOrientation", () => {
  it("is white for a game between models", () => {
    expect(gifOrientation({ players: [seat("white", "model", "a"), seat("black", "model", "b")] }))
      .toBe("white");
  });

  it("puts a person playing black at the bottom", () => {
    expect(gifOrientation({ players: [seat("white", "model", "a"), seat("black", "human", "b")] }))
      .toBe("black");
  });

  it("stays white when people hold both seats", () => {
    expect(gifOrientation({ players: [seat("white", "human", "a"), seat("black", "human", "b")] }))
      .toBe("white");
  });
});

describe("gifCaptions", () => {
  const players = [seat("white", "model", "GPT"), seat("black", "model", "Claude")];

  it("scores a win and puts the ending beside the winner", () => {
    const captions = gifCaptions({ players, result: "0-1", termination: "checkmate" });
    expect(captions.white).toEqual({ name: "GPT", score: "0", ending: null });
    expect(captions.black).toEqual({ name: "Claude", score: "1", ending: "checkmate" });
  });

  it("halves a draw and states the ending once", () => {
    const captions = gifCaptions({ players, result: "1/2-1/2", termination: "stalemate" });
    expect(captions.white.score).toBe("½");
    expect(captions.black.score).toBe("½");
    expect([captions.white.ending, captions.black.ending].filter(Boolean)).toHaveLength(1);
  });

  it("shows no score for an undecided game, but still says why an aborted one stopped", () => {
    const live = gifCaptions({ players, result: "*", termination: null });
    expect(live.white).toEqual({ name: "GPT", score: null, ending: null });

    const aborted = gifCaptions({ players, result: "*", termination: "abandoned" });
    expect(aborted.white.score).toBeNull();
    expect(aborted.white.ending).toBe("abandoned");
  });

  it("names a missing seat by its colour", () => {
    const captions = gifCaptions({ players: [], result: "*", termination: null });
    expect(captions.white.name).toBe("White");
    expect(captions.black.name).toBe("Black");
  });
});

describe("gifFilename", () => {
  it("names both players and the game", () => {
    const players = [
      seat("white", "model", "GPT-5 (high)"),
      seat("black", "model", "Claude Opus 4.1"),
    ];
    expect(gifFilename({ id: "3f9a2c1b-0000-4000-8000-000000000000", players })).toBe(
      "chessmark-gpt-5-high-vs-claude-opus-4-1-3f9a2c1b.gif",
    );
  });

  it("keeps a long name from running away with the filename", () => {
    const players = [seat("white", "model", "x".repeat(200)), seat("black", "model", "y")];
    const name = gifFilename({ id: "abcdef12", players });
    expect(name.length).toBeLessThan(80);
    expect(name).not.toMatch(/--/);
  });
});

describe("encodeGif", () => {
  const solid = (width: number, height: number, rgb: [number, number, number]) => {
    const rgba = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < rgba.length; i += 4) rgba.set([...rgb, 255], i);
    return rgba;
  };

  it("writes one looping image per frame, each with its own delay", () => {
    const delays = gifDelays(5);
    const frames = delays.map((delay, index) => ({
      rgba: solid(16, 12, [index * 40, 100, 200 - index * 30]),
      delay,
    }));
    const bytes = encodeGif(frames, 16, 12, [frames[0]!.rgba, frames.at(-1)!.rgba]);

    const gif = readGif(bytes);
    expect(gif.width).toBe(16);
    expect(gif.height).toBe(12);
    expect(gif.delays).toEqual(delays);
    expect(gif.loops).toBe(true);
  });

  it("shows every frame exactly as it was drawn, including a colour that appears late", () => {
    /* The encoder writes only the pixels that changed. This is the test that says a viewer still
       sees the whole picture — and the colour that arrives on a later frame is black, because the
       first version reserved its "unchanged" slot as a black palette entry, and every black
       pixel a later frame drew went transparent: black pieces vanished as soon as they moved. */
    const width = 8;
    const height = 4;
    const grey = solid(width, height, [120, 110, 90]);
    const withBlack = grey.slice();
    withBlack.set([0, 0, 0, 255], 4 * 5);
    const withWhite = withBlack.slice();
    withWhite.set([255, 255, 255, 255], 4 * 30);

    const frames = [grey, withBlack, withWhite].map((rgba) => ({ rgba, delay: 100 }));
    const gif = readGif(encodeGif(frames, width, height, [grey, withBlack, withWhite]));

    expect(gif.screens).toHaveLength(3);
    gif.screens.forEach((screen, index) => expect([...screen]).toEqual([...frames[index]!.rgba]));
  });
});

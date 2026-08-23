import { describe, expect, it } from "vitest";

import {
  LOOP_PAUSE_PLIES,
  advance,
  boardDomId,
  buildFrames,
  isContiguous,
  positionAt,
  shouldAnimate,
  staggeredStart,
} from "@/lib/animation";

describe("shouldAnimate", () => {
  const base = { reducedMotion: false, visible: true, plies: 40 };

  it("runs for a visible board with moves", () => {
    expect(shouldAnimate(base)).toBe(true);
  });

  /** The Phase 19 criterion: reduced motion creates no timer, rather than ignoring one. */
  it("never runs under prefers-reduced-motion", () => {
    expect(shouldAnimate({ ...base, reducedMotion: true })).toBe(false);
    // Not even when everything else is as favourable as possible.
    expect(shouldAnimate({ reducedMotion: true, visible: true, plies: 500 })).toBe(false);
  });

  it("stops when scrolled out of view", () => {
    expect(shouldAnimate({ ...base, visible: false })).toBe(false);
  });

  it("does not run for a game with no moves", () => {
    expect(shouldAnimate({ ...base, plies: 0 })).toBe(false);
  });
});

describe("staggeredStart", () => {
  it("spreads three boards across their own game", () => {
    expect(staggeredStart(0, 60, 3)).toBe(0);
    expect(staggeredStart(1, 60, 3)).toBe(20);
    expect(staggeredStart(2, 60, 3)).toBe(40);
  });

  it("keeps every start inside the game", () => {
    for (const plies of [1, 7, 19, 53, 300]) {
      for (let i = 0; i < 3; i += 1) {
        const start = staggeredStart(i, plies, 3);
        expect(start).toBeGreaterThanOrEqual(0);
        expect(start).toBeLessThanOrEqual(plies);
      }
    }
  });

  it("is defined for degenerate input", () => {
    expect(staggeredStart(0, 0, 3)).toBe(0);
    expect(staggeredStart(5, 40, 0)).toBe(0);
  });
});

describe("advance", () => {
  it("steps forward one frame at a time", () => {
    expect(advance(0, 10)).toBe(1);
    expect(advance(5, 10)).toBe(6);
  });

  it("dwells on the final position before looping", () => {
    // Frames 0..10 are positions, then LOOP_PAUSE_PLIES of dwell.
    let index = 10;
    for (let i = 0; i < LOOP_PAUSE_PLIES; i += 1) {
      index = advance(index, 10);
      expect(positionAt(index, 10)).toBe(10);
    }
    expect(advance(index, 10)).toBe(0);
  });

  it("always returns a usable index", () => {
    let index = 0;
    for (let i = 0; i < 200; i += 1) {
      index = advance(index, 19);
      expect(positionAt(index, 19)).toBeGreaterThanOrEqual(0);
      expect(positionAt(index, 19)).toBeLessThanOrEqual(19);
    }
  });

  it("does not divide by zero on an empty game", () => {
    expect(advance(0, 0)).toBe(1);
    expect(positionAt(advance(0, 0), 0)).toBe(0);
  });
});

describe("buildFrames", () => {
  const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

  it("includes the starting position, so a game of n plies has n+1 frames", () => {
    expect(buildFrames(START, [])).toHaveLength(1);
    expect(buildFrames(START, ["e4", "c5", "Nf3"])).toHaveLength(4);
  });

  it("has no last move on the opening frame", () => {
    expect(buildFrames(START, ["e4"])[0].lastMove).toBeNull();
  });

  it("records the squares of each move", () => {
    const frames = buildFrames(START, ["e4", "c5"]);
    expect(frames[1].lastMove).toEqual({ from: "e2", to: "e4" });
    expect(frames[2].lastMove).toEqual({ from: "c7", to: "c5" });
  });

  /**
   * The rule `LiveGame` already follows: a move we cannot replay means our view has drifted from
   * the server's, so stop rather than render a position that never existed.
   */
  it("stops at a move it cannot replay rather than skipping it", () => {
    const frames = buildFrames(START, ["e4", "c5", "Qxh8", "Nf3"]);
    expect(frames).toHaveLength(3); // start + e4 + c5, then it stops
    expect(frames[frames.length - 1].lastMove).toEqual({ from: "c7", to: "c5" });
  });

  it("survives junk without throwing", () => {
    expect(buildFrames(START, ["not-a-move"])).toHaveLength(1);
  });
});

describe("isContiguous", () => {
  it("animates a single step forward", () => {
    expect(isContiguous(4, 5)).toBe(true);
  });

  it("does not animate the first paint", () => {
    expect(isContiguous(null, 0)).toBe(false);
    expect(isContiguous(null, 27)).toBe(false);
  });

  /** The loop restart: an endgame becoming an opening is not a move. */
  it("does not animate the wrap back to the start", () => {
    expect(isContiguous(52, 0)).toBe(false);
  });

  /**
   * The reduced-motion resolution: the first paint shows the final position, and the board then
   * jumps to its staggered start. Animating that slides every piece at once.
   */
  it("does not animate a jump from the final position to a staggered start", () => {
    expect(isContiguous(72, 24)).toBe(false);
  });

  it("does not animate standing still, as during the end-of-loop dwell", () => {
    expect(isContiguous(30, 30)).toBe(false);
  });

  it("never animates backwards", () => {
    expect(isContiguous(10, 9)).toBe(false);
    expect(isContiguous(10, 2)).toBe(false);
  });
});

describe("boardDomId", () => {
  it("strips the colons React puts in useId values", () => {
    expect(boardDomId(":r1:")).toBe("cb-r1");
    expect(boardDomId(":R7h:")).toBe("cb-R7h");
  });

  /**
   * The suite runs in node, so this asserts the identifier's shape rather than handing it to a
   * real `querySelector`. A colon or a bracket surviving into `#{id}-square-e2` is the failure
   * that matters, and this catches it.
   */
  it("produces a valid CSS identifier", () => {
    const CSS_IDENT = /^-?[A-Za-z_][A-Za-z0-9_-]*$/;
    for (const raw of [":r1:", ":r12:", "\u00abr0\u00bb", "_R_1_", ":r1:-x", "", "1"]) {
      expect(boardDomId(raw)).toMatch(CSS_IDENT);
    }
  });

  it("never starts with a digit, which is invalid for a CSS identifier", () => {
    expect(boardDomId("1")).toMatch(/^[a-zA-Z]/);
    expect(boardDomId(":r9:")).toMatch(/^[a-zA-Z]/);
  });

  /** Distinct React ids must stay distinct, or boards measure each other again. */
  it("keeps distinct inputs distinct", () => {
    const ids = [":r1:", ":r2:", ":r3:", ":r10:"].map(boardDomId);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

import { describe, expect, it } from "vitest";

import { liveCue, replayCue, soundFor } from "@/lib/sound";

describe("soundFor", () => {
  it.each([
    ["e4", "move"],
    ["Nf3", "move"],
    ["exd5", "capture"],
    ["Nxe5", "capture"],
    ["O-O", "castle"],
    ["O-O-O", "castle"],
    ["e8=Q", "promote"],
    ["Bb5+", "check"],
    ["Qxf7#", "game-end"],
  ])("%s sounds like %s", (san, sound) => {
    expect(soundFor(san)).toBe(sound);
  });

  it("hears the most important thing in a move that is several", () => {
    // A capture that checks is a check; a castle that checks is a check; a promotion that
    // captures is a promotion; a promotion that mates ends the game.
    expect(soundFor("Bxf7+")).toBe("check");
    expect(soundFor("O-O+")).toBe("check");
    expect(soundFor("dxe8=N")).toBe("promote");
    expect(soundFor("e8=Q#")).toBe("game-end");
    // `O-O-O` must not be read as a capture or a move just because it is not `O-O` exactly.
    expect(soundFor("O-O-O")).not.toBe("move");
  });
});

describe("liveCue", () => {
  it("is silent on the first render, however long the history", () => {
    expect(liveCue(null, { plies: 60, ended: false, lastSan: "Qxf7+" })).toBeNull();
    expect(liveCue(null, { plies: 60, ended: true, lastSan: "Qxf7#" })).toBeNull();
  });

  it("plays the move that just arrived", () => {
    const before = { plies: 10, ended: false };
    expect(liveCue(before, { plies: 11, ended: false, lastSan: "Nxe5" })).toBe("capture");
  });

  it("plays one sound for a burst of plies, for the newest", () => {
    const before = { plies: 10, ended: false };
    expect(liveCue(before, { plies: 14, ended: false, lastSan: "O-O" })).toBe("castle");
  });

  it("plays the ending instead of the move that caused it", () => {
    const before = { plies: 10, ended: false };
    expect(liveCue(before, { plies: 11, ended: true, lastSan: "Nf3" })).toBe("game-end");
  });

  it("plays an ending that comes without a move — a forfeit, a resignation", () => {
    const before = { plies: 10, ended: false };
    expect(liveCue(before, { plies: 10, ended: true, lastSan: "Nf3" })).toBe("game-end");
  });

  it("is silent when nothing about the board changed", () => {
    // A re-render for the conversation panel, a stats refetch, a live reasoning frame.
    const before = { plies: 10, ended: false };
    expect(liveCue(before, { plies: 10, ended: false, lastSan: "Nf3" })).toBeNull();
    expect(liveCue({ plies: 10, ended: true }, { plies: 10, ended: true, lastSan: "Nf3" })).toBe(
      null,
    );
  });
});

describe("replayCue", () => {
  const moves = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6"];

  it("plays a single step forward", () => {
    expect(replayCue(0, 1, moves)).toBe("move");
    expect(replayCue(6, 7, moves)).toBe("game-end");
    expect(replayCue(5, 6, moves)).toBe("move");
  });

  it("plays the move landed on, not the one left", () => {
    const captures = ["e4", "d5", "exd5", "Qxd5"];
    expect(replayCue(2, 3, [...captures, "Nc3"])).toBe("capture");
    expect(replayCue(1, 2, [...captures, "Nc3"])).toBe("move");
  });

  it("is silent for anything that is not one step forward", () => {
    expect(replayCue(null, 7, moves)).toBeNull(); // the page opening at the final position
    expect(replayCue(7, 0, moves)).toBeNull(); // play pressed at the end rewinds
    expect(replayCue(3, 2, moves)).toBeNull(); // stepping back
    expect(replayCue(1, 5, moves)).toBeNull(); // a scrubber drag
    expect(replayCue(3, 3, moves)).toBeNull(); // a re-render
  });
});

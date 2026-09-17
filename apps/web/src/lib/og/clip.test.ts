/**
 * Cutting a name to fit a card.
 *
 * Small enough to look trivial, and it is here because the obvious implementation — CSS
 * `text-overflow: ellipsis` — produced a **tofu box** on every truncated row. Satori looks for the
 * `…` glyph in the fonts it was handed, and the only one it was handed is a six-glyph chess subset.
 * Three ASCII periods exist in every face there will ever be.
 */

import { describe, expect, it } from "vitest";

import { clip } from "@/lib/og/clip";

describe("clip", () => {
  it("leaves a name that fits exactly alone", () => {
    expect(clip("nex-agi/nex-n2.5-pro:free", 30)).toBe("nex-agi/nex-n2.5-pro:free");
    expect(clip("a".repeat(30), 30)).toBe("a".repeat(30));
  });

  it("never returns more characters than it was given room for", () => {
    /* The property that matters: the column is a fixed width, and a truncation that overflows it
       is the bug it exists to prevent. `...` counts toward the budget. */
    for (const name of [
      "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
      "inclusionai/ling-3.0-flash-sante:free",
      "a".repeat(200),
    ]) {
      expect(clip(name, 30).length).toBeLessThanOrEqual(30);
    }
  });

  it("marks that something was cut", () => {
    expect(clip("nvidia/nemotron-3-ultra-550b-a55b:free", 30)).toBe("nvidia/nemotron-3-ultra-550...");
  });

  it("uses periods, never an ellipsis character", () => {
    // The whole reason this function exists rather than a CSS property.
    expect(clip("a".repeat(50), 20)).not.toContain("…");
    expect(clip("a".repeat(50), 20)).toContain("...");
  });

  it("does not leave a space stranded before the periods", () => {
    expect(clip("NVIDIA: Nemotron 3 Super (free)", 20)).toBe("NVIDIA: Nemotron...");
  });

  it("handles a budget too small to say anything", () => {
    // Not reachable from the cards, and it must not throw or return something longer than asked.
    expect(clip("anything", 3).length).toBeLessThanOrEqual(3);
    expect(() => clip("anything", 0)).not.toThrow();
  });
});

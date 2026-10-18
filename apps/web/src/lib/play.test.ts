import { describe, expect, it } from "vitest";

import { parsePlay, playHref, playReturn } from "@/lib/play";
import type { ModelInfo } from "@/lib/types";

const model = (id: string, playable = true) =>
  ({ openrouter_id: id, contestants: playable ? [{}] : [] }) as unknown as ModelInfo;

const catalogue = [model("qwen/qwen3.8-27b:free"), model("gone/retired", false)];

describe("playHref and parsePlay", () => {
  it("round-trips a model id, colon and slash included", () => {
    const href = playHref("qwen/qwen3.8-27b:free", "models");
    const params = Object.fromEntries(new URLSearchParams(href.split("?")[1]));
    expect(parsePlay(params, catalogue)).toEqual({
      mode: "models",
      model: "qwen/qwen3.8-27b:free",
    });
  });

  it("defaults to playing the model yourself", () => {
    expect(parsePlay({ model: "qwen/qwen3.8-27b:free" }, catalogue).mode).toBe("human");
    expect(parsePlay({ mode: "nonsense" }, catalogue).mode).toBe("human");
  });

  /* The form can only start what it lists. Pre-selecting anything else would hand the API a game
     it refuses, after the person has filled the form in. */
  it("ignores a model the form cannot start", () => {
    expect(parsePlay({ model: "gone/retired" }, catalogue).model).toBeNull();
    expect(parsePlay({ model: "nobody/nothing" }, catalogue).model).toBeNull();
  });

  it("sends a signed-out visitor back with the choice intact", () => {
    expect(playReturn({ mode: "human", model: "qwen/qwen3.8-27b:free" })).toBe(
      "/play?model=qwen%2Fqwen3.8-27b%3Afree&mode=human",
    );
    expect(playReturn({ mode: "human", model: null })).toBe("/play");
  });
});

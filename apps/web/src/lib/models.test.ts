import { describe, expect, it } from "vitest";

import { browseModels, countModels } from "@/lib/models";
import type { ModelInfo } from "@/lib/types";

function model(openrouter_id: string, overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    id: openrouter_id,
    openrouter_id,
    display_name: openrouter_id,
    provider: openrouter_id.split("/")[0],
    context_length: 128000,
    supports_reasoning: false,
    is_free: false,
    prompt_usd_per_token: "0.000001",
    completion_usd_per_token: "0.000002",
    quantizations: [],
    contestants: [],
    endpoint_count: 1,
    is_floating_alias: false,
    credit_cost: 1,
    ...overrides,
  } as ModelInfo;
}

const CATALOGUE = [
  model("openai/gpt-5-nano", { credit_cost: 1 }),
  model("openai/gpt-5.5-pro", { credit_cost: 6 }),
  model("anthropic/claude-haiku-4.5", { credit_cost: 2 }),
  model("google/gemini-3.7-flash", { credit_cost: 2 }),
];

describe("browseModels", () => {
  it("groups by provider, alphabetically", () => {
    const groups = browseModels(CATALOGUE, "");

    expect(groups.map((g) => g.provider)).toEqual(["anthropic", "google", "openai"]);
  });

  it("sorts models within a provider by slug", () => {
    const groups = browseModels(CATALOGUE, "");
    const openai = groups.find((g) => g.provider === "openai");

    expect(openai?.models.map((m) => m.openrouter_id)).toEqual([
      "openai/gpt-5-nano",
      "openai/gpt-5.5-pro",
    ]);
  });

  it("reports the cheapest model in each group", () => {
    /* Shown on the collapsed row, so a provider can be judged without opening it. */
    const groups = browseModels(CATALOGUE, "");

    expect(groups.find((g) => g.provider === "openai")?.cheapest).toBe(1);
    expect(groups.find((g) => g.provider === "google")?.cheapest).toBe(2);
  });

  it("returns everything for an empty query", () => {
    expect(countModels(browseModels(CATALOGUE, ""))).toBe(4);
  });

  it("treats whitespace as an empty query", () => {
    expect(countModels(browseModels(CATALOGUE, "   "))).toBe(4);
  });

  it("matching a provider shows everything it serves", () => {
    /* "anthropic" is not in any of its model *names*, so matching only the slug would return
       nothing for the most obvious search someone could type. */
    const groups = browseModels(CATALOGUE, "openai");

    expect(groups).toHaveLength(1);
    expect(countModels(groups)).toBe(2);
  });

  it("matching a model name crosses providers", () => {
    const groups = browseModels(CATALOGUE, "pro");

    expect(groups.map((g) => g.provider)).toEqual(["openai"]);
    expect(groups[0].models.map((m) => m.openrouter_id)).toEqual(["openai/gpt-5.5-pro"]);
  });

  it("is case-insensitive", () => {
    expect(countModels(browseModels(CATALOGUE, "GEMINI"))).toBe(1);
    expect(countModels(browseModels(CATALOGUE, "Anthropic"))).toBe(1);
  });

  it("narrows a provider to the matching models when the query is not the provider name", () => {
    const catalogue = [
      model("openai/gpt-5-nano"),
      model("openai/o3-pro"),
      model("openai/gpt-4"),
    ];

    const groups = browseModels(catalogue, "gpt");

    expect(groups[0].models.map((m) => m.openrouter_id)).toEqual([
      "openai/gpt-4",
      "openai/gpt-5-nano",
    ]);
  });

  it("returns nothing when nothing matches", () => {
    expect(browseModels(CATALOGUE, "no-such-model")).toEqual([]);
  });

  it("matches a display name that differs from the slug", () => {
    const catalogue = [model("vendor/x-1", { display_name: "Xylophone One" })];

    expect(countModels(browseModels(catalogue, "xylophone"))).toBe(1);
  });

  it("falls back to a provider label rather than dropping a model", () => {
    const catalogue = [model("bare-name", { provider: "" })];

    const groups = browseModels(catalogue, "");

    expect(groups).toHaveLength(1);
    expect(groups[0].provider).toBe("unknown");
  });
});

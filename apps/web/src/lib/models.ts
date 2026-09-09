/**
 * Browsing the model catalogue.
 *
 * 330 playable models across 36 providers, and `openai` alone holds 86 of them — a flat list is
 * not something a person can choose from. Grouping by provider is the only structure the data
 * already has, and it is the one people actually think in ("what does Anthropic have?").
 *
 * Pure and here rather than in the component for the usual reason: search rules have edge cases
 * worth asserting, and a component is only ever checked by hand.
 */

import type { ModelInfo } from "@/lib/types";

export interface ProviderGroup {
  provider: string;
  models: ModelInfo[];
  /**
   * The lowest credit cost in the group. Shown on the collapsed row so a provider can be judged
   * without opening it — with 36 to scan, "cheapest here is 6 credits" is the fact that decides
   * whether it is worth expanding at all.
   */
  cheapest: number;
}

/**
 * Group the catalogue by provider, keeping only what matches `query`.
 *
 * Two ways to match, because people search for both kinds of thing:
 *
 * * **The provider** — `anthropic` shows everything Anthropic serves, not just models with the
 *   word in their name.
 * * **The model** — `flash` shows every model called flash, across all providers.
 *
 * An empty query returns everything. Providers are ordered alphabetically and models within a
 * provider by slug, so the list does not reshuffle as someone types.
 */
export function browseModels(models: ModelInfo[], query: string): ProviderGroup[] {
  const needle = query.trim().toLowerCase();

  const byProvider = new Map<string, ModelInfo[]>();
  for (const model of models) {
    const provider = model.provider || "unknown";
    const providerMatches = needle !== "" && provider.toLowerCase().includes(needle);
    const modelMatches =
      needle === "" ||
      model.openrouter_id.toLowerCase().includes(needle) ||
      model.display_name.toLowerCase().includes(needle);

    if (!providerMatches && !modelMatches) continue;

    const group = byProvider.get(provider);
    if (group) group.push(model);
    else byProvider.set(provider, [model]);
  }

  return [...byProvider.entries()]
    .map(([provider, group]) => ({
      provider,
      models: [...group].sort((a, b) => a.openrouter_id.localeCompare(b.openrouter_id)),
      cheapest: Math.min(...group.map((model) => model.credit_cost)),
    }))
    .sort((a, b) => a.provider.localeCompare(b.provider));
}

/** How many models a set of groups holds. The count under the search box. */
export function countModels(groups: ProviderGroup[]): number {
  return groups.reduce((total, group) => total + group.models.length, 0);
}

/**
 * The OpenRouter id that a `/models/[...slug]` route names.
 *
 * **A dynamic segment arrives percent-encoded.** `google/gemma-4-31b-it:free` reaches the page as
 * `["google", "gemma-4-31b-it%3Afree"]`, and simply joining those produces a string that is almost
 * the id — which is worse than one that is obviously wrong, because of where the two halves of the
 * page put it:
 *
 * * In a **path** — `/models/google/gemma-4-31b-it%3Afree` — the server decodes it again and finds
 *   the model. That request works, so the page renders with the right name and the right stats.
 * * In a **query value** — `?model=…` — `URLSearchParams` escapes the `%` to `%25`, the API decodes
 *   once, and looks for a model literally called `google/gemma-4-31b-it%3Afree`. There is none, so
 *   it answers **`200 []`**: not an error, nothing thrown, nothing logged. The page shows a model
 *   that has apparently never played a game.
 *
 * Every `:free` model was in that state, which is nearly the whole catalogue. It surfaced only
 * when the leaderboard drill-down was folded into this page (ADR-0034): that page fetched its
 * games through a **path** — `/leaderboard/{slug}/games` — and so had been quietly compensating.
 */
export function modelSlugFromSegments(segments: string[]): string {
  return segments.map(decodeSegment).join("/");
}

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    // A malformed escape names no model we could look up, so hand it back and let the API 404
    // rather than throwing a `URIError` out of a page that would otherwise render.
    return segment;
  }
}

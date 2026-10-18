/**
 * `/play?model=…&mode=…` — opening the game form with a model already chosen.
 *
 * A model page is where somebody decides they want to see a model play, and the form used to make
 * them find it again in a picker of three hundred. The link carries the choice; this reads it back,
 * and reads it **only against the models the form can actually start**: a retired or unplayable
 * id in the URL is ignored rather than pre-selected into a game the API would refuse.
 */

import type { ModelInfo } from "@/lib/types";

export type PlayMode = "human" | "models";

export interface PlayChoice {
  mode: PlayMode;
  /** Pre-selected: your opponent in a human game, White in a game between two models. */
  model: string | null;
}

export function playHref(model: string, mode: PlayMode): string {
  return `/play?${new URLSearchParams({ model, mode })}`;
}

type Params = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function parsePlay(params: Params, models: ModelInfo[]): PlayChoice {
  const mode: PlayMode = first(params.mode) === "models" ? "models" : "human";
  const wanted = first(params.model);
  const model = models.find(
    (candidate) => candidate.openrouter_id === wanted && candidate.contestants.length > 0,
  );
  return { mode, model: model?.openrouter_id ?? null };
}

/** Where sign-in sends you back to: this page, choice included. */
export function playReturn(choice: PlayChoice): string {
  return choice.model ? playHref(choice.model, choice.mode) : "/play";
}

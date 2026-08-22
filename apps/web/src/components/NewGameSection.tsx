"use client";

/**
 * Shows the game form only to signed-in users, and only when Clerk is configured at all.
 *
 * Split from `NewGame` because `Show` is a Clerk component: rendering it without a provider throws,
 * and a clone of this repo with no Clerk keys still has to load the lobby.
 */

import { Show } from "@clerk/nextjs";

import { clerkEnabled } from "@/components/AuthProvider";
import { NewGame } from "@/components/NewGame";
import type { ModelInfo } from "@/lib/types";

export function NewGameSection({ apiUrl, models }: { apiUrl: string; models: ModelInfo[] }) {
  if (!clerkEnabled) return null;

  return (
    <Show when="signed-in">
      <NewGame apiUrl={apiUrl} models={models} />
    </Show>
  );
}

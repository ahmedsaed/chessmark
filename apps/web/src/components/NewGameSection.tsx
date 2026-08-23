"use client";

/**
 * The game form, with the states around it.
 *
 * Split from `NewGame` because `Show` is a Clerk component: rendering it without a provider throws,
 * and a clone of this repo with no Clerk keys still has to load the site.
 *
 * Signed out, this now says so rather than rendering nothing. On the landing page silence was
 * fine — the form was one section among several — but it owns a whole route now, and a blank page
 * is not an answer to "how do I start a game?".
 */

import { Show, SignInButton } from "@clerk/nextjs";

import { clerkEnabled } from "@/components/AuthProvider";
import { NewGame } from "@/components/NewGame";
import type { ModelInfo } from "@/lib/types";

export function NewGameSection({ apiUrl, models }: { apiUrl: string; models: ModelInfo[] }) {
  if (!clerkEnabled) {
    return (
      <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
        Accounts are not configured on this deployment. Games can still be started from the repo
        with <code className="font-mono text-accent">make play</code>.
      </p>
    );
  }

  return (
    <>
      <Show when="signed-in">
        <NewGame apiUrl={apiUrl} models={models} />
      </Show>

      <Show when="signed-out">
        <div className="border border-line-soft bg-surface px-4 py-5">
          <p className="text-sm text-ink-dim">
            Watching needs no account. Starting a game does, because it spends money on a provider.
          </p>
          <div className="mt-4">
            <SignInButton mode="modal">
              <button
                type="button"
                className="border border-accent-deep bg-accent px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-on-accent transition-colors hover:bg-accent-dim"
              >
                Sign in to play
              </button>
            </SignInButton>
          </div>
        </div>
      </Show>
    </>
  );
}

"use client";

/**
 * The two ways to start a game, and the states around them.
 *
 * Split from `NewGame` because `Show` is a Clerk component: rendering it without a provider throws,
 * and a clone of this repo with no Clerk keys still has to load the site.
 *
 * Signed out, this says so rather than rendering nothing. On the landing page silence was fine —
 * the form was one section among several — but it owns a whole route now, and a blank page is not
 * an answer to "how do I start a game?".
 *
 * Playing yourself is the first tab because it is the thing a visitor cannot do anywhere else.
 */

import { Show, SignInButton } from "@clerk/nextjs";
import { useState } from "react";

import { clerkEnabled } from "@/components/AuthProvider";
import { NewGame } from "@/components/NewGame";
import { NewHumanGame } from "@/components/NewHumanGame";
import type { ModelInfo } from "@/lib/types";

type Mode = "human" | "models";

const MODES: { id: Mode; label: string }[] = [
  { id: "human", label: "You vs a model" },
  { id: "models", label: "Two models" },
];

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
        <Chooser apiUrl={apiUrl} models={models} />
      </Show>

      <Show when="signed-out">
        <div className="border border-line-soft bg-surface px-4 py-5">
          <p className="text-sm text-ink-dim">
            Watching needs no account. Starting a game does, because it spends money on a provider —
            a game you play yourself included: the machine seat still calls a provider every turn.
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

function Chooser({ apiUrl, models }: { apiUrl: string; models: ModelInfo[] }) {
  const [mode, setMode] = useState<Mode>("human");

  return (
    <div className="flex flex-col gap-4">
      <div role="tablist" aria-label="What kind of game" className="flex gap-px bg-line-soft">
        {MODES.map((option) => (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={mode === option.id}
            onClick={() => setMode(option.id)}
            className={`flex-1 px-4 py-2 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              mode === option.id
                ? "bg-surface-2 text-ink"
                : "bg-surface text-ink-faint hover:text-ink-dim"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {mode === "human" ? (
        <NewHumanGame models={models} />
      ) : (
        <NewGame apiUrl={apiUrl} models={models} />
      )}
    </div>
  );
}

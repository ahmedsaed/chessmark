"use client";

/**
 * Pause and resume, for the person paying for a game between two models (ADR-0052).
 *
 * Its turns are charged to them as it plays, so they can stop it spending. **Pause lands before
 * the next turn**: a turn in progress finishes and is charged, because it holds the game while it
 * runs — so the button says "pausing" until the board confirms it, rather than claiming a stop
 * that has not happened yet. Nothing here ends a game; resume plays on from the same position.
 *
 * Rendered only where the seat endpoint said this reader pays, which is also the only place Clerk
 * is certain to be mounted — `useAuth` throws without its provider.
 */

import { useAuth } from "@clerk/nextjs";
import { useState } from "react";

import { ApiError, pauseGame, resumeGame } from "@/lib/api";
import type { GameStatus } from "@/lib/types";

export function OwnerControls({
  gameId,
  status,
  heldByOwner,
}: {
  gameId: string;
  status: GameStatus;
  /** Paused by its owner, rather than by a provider, a halt or credit. Only that pause is theirs
   *  to lift. */
  heldByOwner: boolean;
}) {
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* The board's word is the one that counts: once the game says it is held, "pausing" is over. */
  const pausing = asked && !heldByOwner;

  if (status === "finished" || status === "aborted") return null;

  async function act(call: typeof pauseGame) {
    setBusy(true);
    setError(null);
    try {
      const result = await call(gameId, await getToken());
      setAsked(result.pausing);
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message
          : "That did not reach the server. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-none flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {heldByOwner ? (
          <button type="button" disabled={busy} onClick={() => act(resumeGame)} className={ACCEPT}>
            resume
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || pausing}
            onClick={() => act(pauseGame)}
            className={PLAIN}
          >
            {pausing ? "pausing…" : "pause"}
          </button>
        )}
        <p className="font-mono text-meta text-ink-faint">
          {heldByOwner
            ? "Paused. Nothing is spent until you resume."
            : pausing
              ? "Pausing after the move in progress, which is still charged."
              : "Your credit pays for this game. Pause it to stop spending."}
        </p>
      </div>
      {error && <p className="font-mono text-meta text-bad">{error}</p>}
    </div>
  );
}

const PLAIN =
  "border border-line bg-surface px-2 py-1 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink disabled:opacity-40";
const ACCEPT =
  "border border-accent-deep bg-accent px-2 py-1 font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40";

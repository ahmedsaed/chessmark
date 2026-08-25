"use client";

/**
 * The games you are playing (HUMAN-03).
 *
 * A client component even on server-rendered pages, because the answer depends on who is asking:
 * `/games/mine` is the one listing that is not public, and the token that identifies the caller
 * lives in the browser. Rendering it on the server would mean either publishing seats or shipping
 * a token where it does not belong.
 *
 * Renders nothing at all when you are signed out, have no games, or the request fails. This sits
 * above the lobby, and a visitor who has never played should not be shown an empty box explaining
 * a feature they have not used — nor an error about a list they did not ask for.
 */

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { clerkEnabled } from "@/components/AuthProvider";
import { GameCard } from "@/components/GameCard";
import { listMyGames } from "@/lib/api";
import { orderMyGames, waitingOnYou } from "@/lib/mine";
import type { MyGameSummary } from "@/lib/types";

export function MyGames({ heading = "Your games" }: { heading?: string }) {
  if (!clerkEnabled) return null;
  return <Resolved heading={heading} />;
}

function Resolved({ heading }: { heading: string }) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const [games, setGames] = useState<MyGameSummary[]>([]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) return;

    let cancelled = false;
    (async () => {
      try {
        const mine = await listMyGames(await getToken());
        if (!cancelled) setGames(mine);
      } catch {
        // Silence is the right failure here: this section is an extra, and the pages it sits on
        // are readable without it.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, getToken]);

  /* Signing out has to hide the list, and clearing the state from the effect would be a cascading
     render — so the sign-in state gates the render directly instead. */
  if (!isSignedIn || games.length === 0) return null;

  const ordered = orderMyGames(games);
  const waiting = waitingOnYou(games);

  return (
    <section className="mt-14 first:mt-0">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          {heading}
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-[10px] text-ink-faint">
          {waiting > 0 ? `${waiting} waiting on you` : `${games.length} game${games.length === 1 ? "" : "s"}`}
        </span>
      </div>

      <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {ordered.map((game) => (
          <GameCard
            key={game.id}
            game={game}
            seat={game.your_colour}
            yourTurn={game.your_turn}
          />
        ))}
      </ul>
    </section>
  );
}

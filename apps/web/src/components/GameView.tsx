"use client";

/**
 * Chooses between spectating and playing.
 *
 * A game with a human seat looks identical to everyone except the one person holding it, so the
 * decision cannot be made server-side without publishing who plays what. The seat is asked for
 * with the viewer's own token instead, and the answer is theirs alone.
 *
 * Spectators — including spectators of a human game — fall through to `LiveGame` and get exactly
 * what they get today. Watching never needs an account (AUTH-02).
 */

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { LiveGame } from "@/components/LiveGame";
import { PlayableGame } from "@/components/PlayableGame";
import { clerkEnabled } from "@/components/AuthProvider";
import { openDrawOffer } from "@/lib/draw";
import type { GameDetail, GameEvent } from "@/lib/types";

export function GameView(props: {
  game: GameDetail;
  apiUrl: string;
  initialEvents: GameEvent[];
  actions?: React.ReactNode;
  /** From Clerk's `__client_uat` cookie, read on the server. */
  signedIn: boolean;
}) {
  /**
   * **`useAuth` throws without `ClerkProvider`, and the provider is now conditional.**
   *
   * The root layout mounts Clerk only for a session or an identity route, so a signed-out reader
   * has no provider — and a hook call here is not a wrong answer, it is a **500 on a public page**.
   * The cookie the layout already read is passed down instead, so the hook below is reached only
   * where the provider exists by construction.
   */
  const hasHumanSeat = props.game.players.some((player) => player.kind === "human");
  /* **A live game is asked about too, seat or none** (ADR-0052). A signed-in viewer may have
     started a game between two models, and its turns are charged to them as it plays; only the
     seat endpoint can say so, since who started a game is not public. One request per page load,
     for signed-in viewers of a game still in progress — a finished game spends nothing more. */
  const live = !["finished", "aborted"].includes(props.game.status);
  if (!clerkEnabled || !props.signedIn || !(hasHumanSeat || live)) {
    return <LiveGame {...props} />;
  }
  return <Resolved {...props} />;
}

function Resolved(props: {
  game: GameDetail;
  apiUrl: string;
  initialEvents: GameEvent[];
  actions?: React.ReactNode;
}) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const [seat, setSeat] = useState<"white" | "black" | null>(null);
  const [pays, setPays] = useState(false);

  useEffect(() => {
    if (!isSignedIn) return;

    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        const response = await fetch(`${props.apiUrl}/games/${props.game.id}/seat`, {
          headers: { authorization: `Bearer ${token}`, accept: "application/json" },
        });
        if (!response.ok || cancelled) return;
        const body = (await response.json()) as {
          colour: "white" | "black" | null;
          pays?: boolean;
        };
        setSeat(body.colour);
        setPays(Boolean(body.pays));
      } catch {
        // Failing to resolve a seat means spectating, which is the safe answer: the worst case is
        // a player who has to reload, not a spectator who can move someone else's pieces.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isSignedIn, getToken, props.apiUrl, props.game.id]);

  if (!isLoaded || seat === null) {
    return <LiveGame {...props} pays={pays} />;
  }

  return (
    <PlayableGame
      {...props}
      pays={pays}
      seat={seat}
      drawOffered={openDrawOffer(props.initialEvents, props.game.ply_count) === "opponent"}
    />
  );
}

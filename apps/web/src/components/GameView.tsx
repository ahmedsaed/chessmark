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
}) {
  const hasHumanSeat = props.game.players.some((player) => player.kind === "human");
  if (!clerkEnabled || !hasHumanSeat) {
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
        const body = (await response.json()) as { colour: "white" | "black" | null };
        setSeat(body.colour);
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
    return <LiveGame {...props} />;
  }

  return (
    <PlayableGame
      {...props}
      seat={seat}
      drawOffered={openDrawOffer(props.initialEvents, props.game.ply_count) === "opponent"}
    />
  );
}

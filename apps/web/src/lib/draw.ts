/**
 * Whether a draw offer is still standing.
 *
 * An offer lapses the moment a move is played, exactly as it does over a board, so it counts only
 * if it was made at the position currently on it. Derived from the event log rather than tracked
 * separately, because that log is the one source live, reconnect and replay all read (ADR-0008).
 */

import type { GameEvent } from "@/lib/types";

export type DrawOfferSource = "you" | "opponent" | null;

export function openDrawOffer(events: GameEvent[], plyCount: number): DrawOfferSource {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event.type === "move_made") return null;
    if (event.type !== "draw_offered") continue;

    const ply = Number(event.payload.ply ?? -1);
    if (ply !== plyCount) return null;
    return event.payload.human === true ? "you" : "opponent";
  }
  return null;
}

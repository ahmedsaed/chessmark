/**
 * Replay: the game as it stood after any given ply.
 *
 * The whole design rests on one decision — **replay does not have its own state machine**. It
 * truncates the event list and hands it to the same `foldEvents` the live view uses (ADR-0008).
 * Scrubbing to ply N therefore reproduces, by construction, exactly what a spectator watching
 * live saw the instant ply N landed. A separate "replay reducer" would be a second implementation
 * of the same rules, free to drift from the first, and the drift would be invisible until someone
 * compared a replay against a recording.
 */

import type { GameEvent } from "@/lib/types";

/** How many plies the log contains. The scrubber's upper bound. */
export function plyCount(events: GameEvent[]): number {
  let count = 0;
  for (const event of events) if (event.type === "move_made") count += 1;
  return count;
}

/**
 * Every event up to and including the one that produced ply `ply`.
 *
 * Ply 0 is the starting position: nothing has been thought or said yet, so the conversation is
 * empty. That is the honest rendering — the first model's reasoning happened *before* ply 1
 * landed, but showing it at ply 0 would put a plan on screen before the position it reasons about.
 *
 * At the final ply the tail is included: `game_ended`, and any turn that ended without a move
 * (a forfeit, a timeout). Those belong to the final state and have no later ply to attach to, so
 * truncating at the last `move_made` would silently drop the ending.
 */
export function eventsThroughPly(events: GameEvent[], ply: number): GameEvent[] {
  if (ply <= 0) return [];

  let seen = 0;
  for (let index = 0; index < events.length; index += 1) {
    if (events[index].type !== "move_made") continue;
    seen += 1;
    if (seen === ply) {
      // The last ply carries the tail; any earlier one stops at its own move.
      return seen === plyCount(events) ? events : events.slice(0, index + 1);
    }
  }

  return events;
}

/**
 * The turn id that produced each ply, from `/games/{id}/turns`.
 *
 * Events carry no turn id, so the raw-transcript inspector needs this bridge to know what to
 * fetch. Matching on `ply_number` rather than adding a field to `turn_started` keeps every game
 * already in the database inspectable, including ones recorded before this page existed.
 *
 * A ply can have more than one turn behind it: a provider failure rolls its turn back and the ply
 * is replayed. The later row is the one that actually produced the move, so it wins.
 */
export function turnIdsByPly(turns: { id: number; ply_number: number | null }[]): Map<number, number> {
  const byPly = new Map<number, number>();
  for (const turn of turns) {
    if (turn.ply_number === null) continue;
    const existing = byPly.get(turn.ply_number);
    if (existing === undefined || turn.id > existing) byPly.set(turn.ply_number, turn.id);
  }
  return byPly;
}

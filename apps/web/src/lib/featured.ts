/**
 * Which game the lobby hero shows, and how the featured card names a turn (ADR-0070).
 *
 * Here rather than in the page because the order is the decision, and it is worth asserting: a
 * live game always wins, the operator's pick comes next, and the most recent finished game is the
 * fallback it has always been.
 */

import type { Featured, GameSummary } from "@/lib/types";

export interface HeroChoice {
  id: string;
  /** Set only when the hero is showing the pick. A live game is never dressed as a featured one. */
  featured: Featured | null;
  /** Whether the game can never move again, so its detail may be read from the cache. A pick is
   *  always finished — the API refuses anything else. */
  settled: boolean;
}

/**
 * The hero's game.
 *
 * **Live first, always.** The pick is for the time between games; a game being played right now is
 * the thing the site exists to show, and putting a week-old ending above it would bury it.
 */
export function chooseHero(
  live: GameSummary[],
  featured: Featured | null,
  settled: GameSummary[],
): HeroChoice | null {
  if (live[0]) return { id: live[0].id, featured: null, settled: false };
  if (featured) return { id: featured.game_id, featured, settled: true };
  const recent = settled[0];
  if (recent) return { id: recent.id, featured: null, settled: recent.status === "finished" };
  return null;
}

/**
 * A ply as a reader writes it: `32. Qg8#` for White, `31… Qd8` for Black.
 *
 * Numbered from the game's own starting position. A game that starts with Black to move numbers
 * its first ply as Black's, and counting from White would put every quote one move out.
 */
export function moveLabel(ply: number, san: string, startFen: string): string {
  const blackStarts = startFen.split(" ")[1] === "b";
  const index = ply - 1 + (blackStarts ? 1 : 0);
  const number = Math.floor(index / 2) + 1;
  return index % 2 === 0 ? `${number}. ${san}` : `${number}… ${san}`;
}

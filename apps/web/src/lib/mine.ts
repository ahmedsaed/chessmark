/**
 * Ordering the games you are playing.
 *
 * Lives here rather than inside the component for the reason every other rule in `lib/` does:
 * components are covered by a browser pass and nothing else, so a rule that matters has to be
 * somewhere a test can reach it.
 */

import type { MyGameSummary } from "@/lib/types";

/**
 * Waiting on you first, then your other running games, then what is finished.
 *
 * The whole point of the list is answering "where do I have a move to make", so a game waiting on
 * you outranks one waiting on a model even if it is older. Within a band the server's order —
 * newest first — is preserved, which is why the sort has to be stable.
 */
export function orderMyGames(games: MyGameSummary[]): MyGameSummary[] {
  return [...games].sort((a, b) => band(a) - band(b));
}

function band(game: MyGameSummary): number {
  if (game.your_turn) return 0;
  if (game.status === "running") return 1;
  return 2;
}

/** How many of these are waiting on you. Zero means the list is a record, not a to-do. */
export function waitingOnYou(games: MyGameSummary[]): number {
  return games.filter((game) => game.your_turn).length;
}

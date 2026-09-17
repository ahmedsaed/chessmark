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
  // A paused game bands with a running one. It is not over, and dropping it to the bottom would
  // file it with the games a person is done with.
  if (game.status === "running" || game.status === "paused") return 1;
  return 2;
}

/** How many of these are waiting on you. Zero means the list is a record, not a to-do. */
export function waitingOnYou(games: MyGameSummary[]): number {
  return games.filter((game) => game.your_turn).length;
}

/** Your record over the games that reached a result. */
export interface Record {
  /** Games with a result — the denominator of W/D/L, and not the length of the list. */
  decided: number;
  wins: number;
  draws: number;
  losses: number;
  /** Running, paused or pending. Counted apart rather than filed as a draw. */
  unfinished: number;
  /** Ended with no result: aborted, or a harness stop. Not a finding about anyone. */
  undecided: number;
}

/**
 * W / D / L from the caller's own side of each game.
 *
 * **Three buckets, not two.** A game that is still running and a game the harness stopped are
 * both "not a win and not a loss", and rolling either into draws would inflate a record with
 * games nobody played to an end — the same distinction `bench/ratable.py` makes on the server and
 * the model page makes in its *Played, did not count* section (invariant 11: our ceilings are not
 * findings about a player).
 *
 * Reads `result` rather than `winner_colour`, because a draw has no winner and the two would
 * otherwise disagree about `1/2-1/2`.
 */
export function recordOf(games: MyGameSummary[]): Record {
  const record: Record = { decided: 0, wins: 0, draws: 0, losses: 0, unfinished: 0, undecided: 0 };

  for (const game of games) {
    if (game.status !== "finished") {
      // `aborted` is over but undecided; everything else that is not finished is still going.
      if (game.status === "aborted") record.undecided += 1;
      else record.unfinished += 1;
      continue;
    }
    if (game.result === "*") {
      // Finished with no result is a harness stop — a budget, a ply cap, a provider we could not
      // reach. It ended the game; it says nothing about how the person played.
      record.undecided += 1;
      continue;
    }

    record.decided += 1;
    if (game.result === "1/2-1/2") record.draws += 1;
    else if (game.result === (game.your_colour === "white" ? "1-0" : "0-1")) record.wins += 1;
    else record.losses += 1;
  }

  return record;
}

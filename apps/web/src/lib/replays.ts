/**
 * Choosing which finished games to show as replays.
 *
 * "Non-error" means the game ended the way a game of chess ends, rather than the way a harness
 * stops one. A ply-cap draw or a budget stop is a real record and stays browsable, but it is a
 * poor advertisement for a replay: nothing happened at the end of it.
 *
 * Forfeits are excluded for a different reason. `error_forfeit` says the harness or the endpoint
 * broke, not that a model was outplayed — ADR-0015 abandons those games precisely because the
 * result is not about the model.
 */

import type { GameSummary } from "@/lib/types";

/** Terminations that represent a genuine finish. Everything else is a stop, not an ending. */
export const CLEAN_TERMINATIONS = new Set([
  "checkmate",
  "resignation",
  "stalemate",
  "threefold_repetition",
  "fifty_move_rule",
  "insufficient_material",
]);

/**
 * The scripted stub used for local development and tests (`agents/scripted.py`).
 *
 * It plugs in where a provider would and plays a fixed seven-ply mate, so its games are genuine
 * `checkmate` records with no model behind them. Two of them surfaced in the replay row on the
 * first render, which is what this exists to prevent.
 */
function isScripted(game: GameSummary): boolean {
  return game.players.some((player) => player.model === null || player.model.startsWith("scripted/"));
}

export function isCleanFinish(game: GameSummary): boolean {
  return (
    game.status === "finished" &&
    game.termination !== null &&
    CLEAN_TERMINATIONS.has(game.termination) &&
    game.players.length === 2 &&
    !isScripted(game) &&
    !game.players.some((player) => player.forfeited)
  );
}

/**
 * `count` clean games, chosen at random without repeats.
 *
 * `random` is injectable so the choice can be asserted rather than hoped for. Fisher-Yates over a
 * copy: sorting by a random comparator is the usual shortcut and it is measurably biased.
 */
export function pickReplays(
  games: GameSummary[],
  count = 3,
  random: () => number = Math.random,
): GameSummary[] {
  const pool = games.filter(isCleanFinish);

  for (let i = pool.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }

  return pool.slice(0, count);
}

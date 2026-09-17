/**
 * Which game a card should draw.
 *
 * **The opening position is not an illustration of anything.** Four cards drew it as furniture, and
 * on a site whose whole pitch is *these models are playing right now* a board with thirty-two
 * pieces on their starting squares says the opposite. A card should show a position this URL is
 * actually about.
 *
 * The rule is the same one the landing page uses to choose its hero: **a live game if there is one,
 * otherwise the most recent.** A live board is the best thing the site has to show, and when
 * nothing is live the newest finished game is what a visitor would have seen anyway.
 *
 * Scoped per card: the tournaments index picks from every game, a tournament's card picks from that
 * event's games, so each board is about the page it sits on rather than about the site in general.
 */

import { getGame } from "@/lib/api";
import { START_PLACEMENT } from "@/lib/og/fen";
import type { GameSummary } from "@/lib/types";

/** When a game happened, for ordering. `ended_at` for a finished game, `started_at` while it runs. */
function when(game: GameSummary): number {
  const stamp = game.ended_at ?? game.started_at ?? game.created_at;
  const parsed = Date.parse(stamp);
  // An unparseable timestamp sorts oldest rather than throwing inside image generation.
  return Number.isNaN(parsed) ? 0 : parsed;
}

/**
 * The game worth drawing, or `null` when there is none.
 *
 * Live first — and among several live games, the one furthest along, because a board with pieces
 * developed says more than one three plies in. Otherwise the most recent, whatever its result.
 *
 * Pure, and separated from the fetch below, because *which* game is the decision worth testing and
 * fetching it is not.
 */
export function pickGame(games: GameSummary[]): GameSummary | null {
  const live = games.filter((game) => game.status === "running");
  if (live.length > 0) {
    return live.reduce((best, game) => (game.ply_count > best.ply_count ? game : best));
  }

  const played = games.filter((game) => game.ply_count > 0);
  if (played.length === 0) return null;

  return played.reduce((best, game) => (when(game) > when(best) ? game : best));
}

/**
 * That game's position, ready to draw.
 *
 * Falls back to the opening position when there is nothing to show — a site with no games yet, or
 * a record that has gone. That is the one case where the starting board is honest: it is what an
 * empty Chessmark looks like.
 *
 * Costs one extra read, because a `GameSummary` carries no FEN and only the detail does.
 */
export async function featuredFen(games: GameSummary[]): Promise<string> {
  const game = pickGame(games);
  if (!game) return START_PLACEMENT;

  const detail = await getGame(game.id);
  return detail?.current_fen ?? START_PLACEMENT;
}

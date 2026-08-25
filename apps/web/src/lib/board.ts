/**
 * Which squares a piece can actually reach.
 *
 * Pure, and here rather than in the component for the usual reason: it is the rule the board draws
 * dots from and the rule click-to-move accepts a second click against, so it is worth asserting
 * directly instead of clicking through.
 *
 * `chess.js` only generates moves for the side to move, which does the access control for free:
 * clicking an opponent's piece — or any piece when it is not your turn — yields nothing to select.
 * The server checks the move again regardless (invariant 1); this is a courtesy, not the rule.
 */

import { Chess, type Square } from "chess.js";

export interface LegalTarget {
  to: string;
  /** Drawn as a ring around the piece rather than a dot on the square. */
  capture: boolean;
}

/**
 * Every legal destination for the piece on `square`, or `[]` if it has none.
 *
 * Returns `[]` rather than throwing on a square with no piece, an unreachable position, or a
 * malformed FEN: a board that cannot answer "where can this go" should offer no move, not break
 * the page around it.
 */
export function legalTargets(fen: string, square: string): LegalTarget[] {
  let board: Chess;
  try {
    board = new Chess(fen);
  } catch {
    return [];
  }

  try {
    return dedupeTargets(board.moves({ square: square as Square, verbose: true }));
  } catch {
    // chess.js throws on a square name it does not recognise.
    return [];
  }
}

/**
 * One entry per destination.
 *
 * A promotion generates four moves to the same square — one per piece. The board promotes to a
 * queen (see `LiveGame.handleDrop`), so four identical dots on one square would be four copies of
 * the same choice.
 */
function dedupeTargets(moves: { to: string; captured?: string; flags: string }[]): LegalTarget[] {
  const seen = new Map<string, LegalTarget>();
  for (const move of moves) {
    if (seen.has(move.to)) continue;
    seen.set(move.to, {
      to: move.to,
      // `e` is en passant: a capture whose target square holds no piece.
      capture: Boolean(move.captured) || move.flags.includes("e"),
    });
  }
  return [...seen.values()];
}

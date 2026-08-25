/**
 * What each side has taken, and who is up on material.
 *
 * Derived from the position rather than by tallying capture events, because the position is the
 * thing the board already trusts (`LiveGame` replays SAN to build it). A tally would be a second
 * source of truth that could drift from the first, and it would need the whole event log to answer
 * a question one FEN already answers.
 */

const START_COUNTS: Record<string, number> = { p: 8, n: 2, b: 2, r: 2, q: 1 };

/** Standard relative values. The king is not counted — it is never captured. */
const VALUES: Record<string, number> = { p: 1, n: 3, b: 3, r: 5, q: 9 };

/** Heaviest first, so a rook reads before a pawn. */
const ORDER = ["q", "r", "b", "n", "p"];

export interface Captures {
  /** Pieces White has taken, as lowercase black piece letters, heaviest first. */
  white: string[];
  black: string[];
  /**
   * Material difference in pawns, from White's point of view. Positive means White is up.
   * Shown as `+3` beside whoever is ahead, and by nobody when it is level.
   */
  advantage: number;
}

/**
 * Count what is missing from the starting position.
 *
 * Promotions are why this floors at zero rather than trusting the subtraction: a side that
 * promotes has *more* queens than it started with, and a negative count would read as the
 * opponent having captured a piece that never existed. The pawn that was spent is already counted
 * as missing, so the material total stays right.
 */
export function captures(fen: string): Captures {
  const placement = fen.split(" ")[0] ?? "";

  const live: Record<string, number> = {};
  for (const character of placement) {
    if (/[a-zA-Z]/.test(character)) {
      live[character] = (live[character] ?? 0) + 1;
    }
  }

  const taken = (byWhite: boolean): string[] => {
    // White's captures are the black pieces that are missing, and the other way round.
    const out: string[] = [];
    for (const piece of ORDER) {
      const key = byWhite ? piece : piece.toUpperCase();
      const missing = Math.max(0, (START_COUNTS[piece] ?? 0) - (live[key] ?? 0));
      for (let i = 0; i < missing; i += 1) out.push(piece);
    }
    return out;
  };

  const white = taken(true);
  const black = taken(false);

  const worth = (pieces: string[]) =>
    pieces.reduce((total, piece) => total + (VALUES[piece] ?? 0), 0);

  return { white, black, advantage: worth(white) - worth(black) };
}

/**
 * The key into `react-chessboard`'s own piece set, e.g. `bQ` for a black queen.
 *
 * **Not a Unicode glyph.** ♟♜♞ resolve to whichever system font wins and some platforms render
 * them as emoji — the exact mistake the replay thumbnails made and had to undo (Phase 19). The
 * captured pieces are drawn with the same SVGs the board draws, so they cannot look like a
 * different game to a different reader.
 */
export function pieceKey(piece: string, colour: "white" | "black"): string {
  return `${colour === "white" ? "w" : "b"}${piece.toUpperCase()}`;
}

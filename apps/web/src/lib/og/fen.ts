/**
 * FEN to something a board can be drawn from.
 *
 * Separate from `board.tsx` because it is the only part of drawing a board that is *logic*, and
 * logic is what this project unit-tests — components are Playwright's (`vitest.config.mts`). Left
 * in the component file it was neither: imported by a test, counted by coverage, and dragging a
 * JSX module into a report that is meant to measure rules.
 */

/** The filled glyphs, `U+265A`–`U+265F` — the only six in the vendored subset. */
const GLYPH: Record<string, string> = {
  k: "\u265a",
  q: "\u265b",
  r: "\u265c",
  b: "\u265d",
  n: "\u265e",
  p: "\u265f",
};

export const START_PLACEMENT = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR";

export interface Square {
  glyph: string;
  white: boolean;
}

/**
 * A FEN's placement field to eight ranks of eight, rank 8 first.
 *
 * Takes a whole FEN or a bare placement field, because callers have both: a game record carries
 * `current_fen`, and a card with no game to show wants the opening position by name.
 *
 * **A malformed FEN must not throw.** This runs inside image generation, where an exception is not
 * an error page but a broken-image box beside a link that works — on somebody else's timeline, for
 * as long as the card is cached. Short ranks are padded and long ones trimmed rather than rejected.
 */
export function ranks(fen: string): (Square | null)[][] {
  const placement = fen.split(" ")[0] ?? "";

  return Array.from({ length: 8 }, (_, index) => {
    const rank = placement.split("/")[index] ?? "";
    const cells: (Square | null)[] = [];

    for (const character of rank) {
      if (character >= "1" && character <= "8") {
        for (let empty = 0; empty < Number(character); empty += 1) cells.push(null);
      } else {
        cells.push({
          glyph: GLYPH[character.toLowerCase()] ?? "",
          white: character === character.toUpperCase(),
        });
      }
    }

    return cells.length >= 8
      ? cells.slice(0, 8)
      : [...cells, ...Array<null>(8 - cells.length).fill(null)];
  });
}

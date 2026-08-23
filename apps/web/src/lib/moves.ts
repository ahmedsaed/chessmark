/**
 * Formatting move lists for display.
 *
 * Lives here rather than in the component because it is pure logic with edge cases worth
 * testing — pairing, numbering, and trimming all have off-by-one traps.
 */

/**
 * The last few plies as numbered pairs: `… 21.Nf3 Nc6  22.d4 cxd4`.
 *
 * Openings are the part a reader can judge at a glance, but by ply 40 the interesting moves are
 * the recent ones — so this shows the tail, with a leading ellipsis when it has trimmed.
 *
 * The window always starts on a **white** move. Slicing at an odd index would number Black's
 * reply as if it were White's, which reads as a transcription error rather than a trim.
 */
export function tailMoves(moves: string[], plies = 12): string {
  if (moves.length === 0) return "";

  const wanted = Math.max(0, moves.length - plies);
  const from = wanted % 2 === 0 ? wanted : wanted - 1;
  const shown = moves.slice(from);

  const pairs: string[] = [];
  for (let i = 0; i < shown.length; i += 2) {
    const number = (from + i) / 2 + 1;
    pairs.push(`${number}.${shown[i]}${shown[i + 1] ? ` ${shown[i + 1]}` : ""}`);
  }

  return `${from > 0 ? "… " : ""}${pairs.join("  ")}`;
}

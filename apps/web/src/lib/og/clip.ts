/**
 * Cut a name to fit a fixed column on a social card.
 *
 * **In the text, not in CSS.** `text-overflow: ellipsis` is the obvious answer and it draws a tofu
 * box: Satori looks for the `…` glyph in the fonts it was given, and the only one it was given is
 * the six-glyph chess subset the pieces need. Three ASCII periods exist in every face there will
 * ever be.
 *
 * Its own module rather than a helper inside `shell.tsx` for the reason `fen.ts` is: this is logic,
 * and logic is the part of a card this project unit-tests — components stay Playwright's.
 */

/** The marker, and the budget it spends. */
const MARKER = "...";

export function clip(text: string, max: number): string {
  if (text.length <= max) return text;
  if (max <= MARKER.length) return text.slice(0, Math.max(max, 0));

  return `${text.slice(0, max - MARKER.length).trimEnd()}${MARKER}`;
}

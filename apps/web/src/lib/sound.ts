import { isContiguous } from "@/lib/animation";

/**
 * Which sound a ply makes, and when a board makes one at all.
 *
 * The decisions live here, away from the Web Audio plumbing in `hooks/useMoveSounds.ts`, because
 * they are the part that can be wrong in a way nobody notices until it is annoying: a page that
 * clatters through forty moves of history on load, a scrubber drag that fires a sound per ply it
 * passes, a checkmate that plays "move" and then "game over" on top of it. Each is a rule below
 * with a test.
 */

export type SoundName = "move" | "capture" | "castle" | "check" | "promote" | "game-end";

export const SOUND_NAMES: readonly SoundName[] = [
  "move",
  "capture",
  "castle",
  "check",
  "promote",
  "game-end",
];

/**
 * The sound for one move, read from its SAN.
 *
 * SAN already carries everything this needs — `x`, `O-O`, `=Q`, `+`, `#` — so no board is
 * replayed to decide it. The order is what a listener would want to hear when a move is several
 * things at once: a capture that gives check is heard as a check, a promotion that mates as the
 * end of the game.
 */
export function soundFor(san: string): SoundName {
  if (san.includes("#")) return "game-end";
  if (san.includes("+")) return "check";
  if (san.includes("=")) return "promote";
  if (san.startsWith("O-O")) return "castle";
  if (san.includes("x")) return "capture";
  return "move";
}

/**
 * What a live board should play as its state moves from `before` to `after`.
 *
 * `before` is `null` on the first render, which plays nothing: the moves a page loads with are
 * history, and a game opened at ply 60 must not open with a sound as if a move had just landed.
 *
 * Several plies at once (a reconnect catching up) make one sound, for the newest, rather than a
 * burst. An ending outranks the move that caused it — the game-end sound is itself a piece being
 * set down, so playing both would be two sounds for one event.
 */
export function liveCue(
  before: { plies: number; ended: boolean } | null,
  after: { plies: number; ended: boolean; lastSan: string | null },
): SoundName | null {
  if (before === null) return null;
  if (after.ended && !before.ended) return "game-end";
  if (after.plies > before.plies && after.lastSan) return soundFor(after.lastSan);
  return null;
}

/**
 * What the replay should play when the shown ply moves from `previous` to `next`.
 *
 * Only a single step forward is heard — the same rule that decides whether the board animates
 * (`isContiguous`). Stepping back, dragging the scrubber, and the rewind when play is pressed at
 * the end are jumps, and a jump of twenty plies has no one move to sound like. Arriving at the
 * last ply of the game plays its ending, since the replay is only ever of a finished game.
 */
export function replayCue(
  previous: number | null,
  next: number,
  moves: readonly string[],
): SoundName | null {
  if (!isContiguous(previous, next)) return null;
  if (next >= moves.length) return "game-end";
  const san = moves[next - 1];
  return san ? soundFor(san) : null;
}

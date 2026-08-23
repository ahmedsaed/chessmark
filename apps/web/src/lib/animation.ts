import { Chess } from "chess.js";

/**
 * Timing and gating for the auto-playing replay boards (Phase 19).
 *
 * The decisions live here rather than inside the component because they are the part worth
 * asserting: whether a timer runs at all, where each board starts, how the index wraps, and what
 * happens to a move that will not replay. A hook needs a DOM to test; these are functions.
 */

/** How long one ply stays on screen. Slow enough to read a move, quick enough to feel alive. */
export const PLY_INTERVAL_MS = 750;

/** Piece slide. Must stay under `PLY_INTERVAL_MS` or moves queue up behind the animation. */
export const PLY_ANIMATION_MS = 280;

/** Delay between neighbouring boards' first tick, so the row does not step in unison. */
export const STAGGER_MS = 240;

/** How long the final position holds before the loop restarts. */
export const LOOP_PAUSE_PLIES = 3;

/**
 * Where board `index` starts within its own game.
 *
 * Every board starting at ply 0 would show three near-identical opening positions — the same
 * eight pawns, three times. Spreading the start across each game's own length means the row shows
 * an opening, a middlegame, and an endgame at any given moment.
 */
export function staggeredStart(index: number, plies: number, count: number): number {
  if (plies <= 0 || count <= 0) return 0;
  return Math.floor((plies * (index % count)) / count);
}

/**
 * The next index in the loop.
 *
 * The cycle runs one position per ply plus `LOOP_PAUSE_PLIES` of dwell on the final position, so
 * a game does not snap back to the opening the instant it ends. Positions are indexed 0..plies
 * inclusive — index 0 is the starting position — hence `plies + 1` real frames.
 */
export function advance(index: number, plies: number): number {
  const frames = plies + 1 + LOOP_PAUSE_PLIES;
  if (frames <= 0) return 0;
  return (index + 1) % frames;
}

/**
 * Which position a cycle index refers to, clamping the dwell frames onto the final position.
 */
export function positionAt(index: number, plies: number): number {
  return Math.min(index, plies);
}

/**
 * Whether a board should be running a timer at all.
 *
 * `reducedMotion` wins outright: the criterion is that no timer is created, not that one is
 * created and then ignored. Off-screen boards stop for the same reason — a row scrolled past
 * should not keep waking the main thread.
 */
export function shouldAnimate({
  reducedMotion,
  visible,
  plies,
}: {
  reducedMotion: boolean;
  visible: boolean;
  plies: number;
}): boolean {
  if (reducedMotion) return false;
  if (!visible) return false;
  return plies > 0;
}

export interface Frame {
  fen: string;
  lastMove: { from: string; to: string } | null;
}

/**
 * Every position of the game, starting position first.
 *
 * A move that will not replay stops the sequence rather than skipping it — the rule `LiveGame`
 * already follows. Rendering the rest would show a position that never occurred.
 */
export function buildFrames(startFen: string, moves: string[]): Frame[] {
  const board = new Chess(startFen);
  const frames: Frame[] = [{ fen: board.fen(), lastMove: null }];

  for (const san of moves) {
    try {
      const move = board.move(san);
      frames.push({ fen: board.fen(), lastMove: { from: move.from, to: move.to } });
    } catch {
      break;
    }
  }

  return frames;
}


/**
 * Whether moving from one position to the next is a single ply, and so worth animating.
 *
 * Everything else is a jump, and a board asked to animate a jump slides every piece across the
 * squares at once — which reads as pieces overshooting and then snapping into place. Two jumps
 * happen in normal use: the loop restart, where an endgame becomes an opening, and the first
 * render after the reduced-motion query resolves, where the final position becomes the staggered
 * start. Both must land instantly.
 *
 * `previous` is `null` on the very first paint, which is a jump by definition — there is nothing
 * to animate from.
 */
export function isContiguous(previous: number | null, next: number): boolean {
  if (previous === null) return false;
  return next === previous + 1;
}

/**
 * A DOM id for one board instance, safe to use inside a CSS selector.
 *
 * `react-chessboard` measures its squares with a bare
 * `document.querySelector('#{id}-square-{square}')`, and its `id` option defaults to the constant
 * `"chessboard"`. With more than one board mounted, every instance therefore measures **the first
 * board in the document**. On the landing page that is the 440px hero, so the 120px thumbnails
 * animated with 55px squares and threw their pieces most of the way across the board before
 * snapping into place.
 *
 * `useId()` is the correct source of uniqueness — stable across SSR and hydration — but it returns
 * values like `:r1:`, and a colon is a combinator in a CSS selector. Stripping to alphanumerics
 * and prefixing keeps it a valid identifier, since an id may not begin with a digit.
 */
export function boardDomId(reactId: string): string {
  return `cb-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;
}

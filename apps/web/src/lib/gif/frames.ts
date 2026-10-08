/**
 * What a game's GIF shows, frame by frame — the part of the export that is logic rather than
 * pixels.
 *
 * Separate from `draw.ts` for the reason `og/fen.ts` is separate from `og/board.tsx`: this is the
 * part with rules in it, and rules are what the unit suite asserts. Drawing needs a canvas, which
 * only the browser suite has.
 */

import { Chess } from "chess.js";

import { endingLabel } from "@/lib/archive";
import type { Colour, GameDetail } from "@/lib/types";

export interface GifFrame {
  fen: string;
  /** Highlighted on the board, as the page highlights it. Null on the opening frame. */
  lastMove: { from: string; to: string } | null;
}

/**
 * One frame per ply, plus the position before the first move.
 *
 * Replayed from `start_fen` rather than assumed to start from the standard position: a game can
 * begin anywhere, and SAN is only meaningful against the position it was written in.
 *
 * **A move that does not replay ends the GIF there instead of throwing.** The server validated
 * every move (invariant 1), so this should never happen — but if it does, an export of the moves
 * that *do* replay is honest, and a button that silently does nothing is not.
 */
export function gifFrames(startFen: string, moves: string[]): GifFrame[] {
  let board: Chess;
  try {
    board = new Chess(startFen);
  } catch {
    return [];
  }

  const frames: GifFrame[] = [{ fen: board.fen(), lastMove: null }];
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

/** How long each frame stays up, in milliseconds. */
export const DELAY = {
  /** The opening position: long enough to read the names before anything moves. */
  first: 1000,
  ply: 800,
  /** The final position is the point of the GIF, so it is held before the loop restarts. */
  last: 3000,
} as const;

export function gifDelays(frameCount: number): number[] {
  return Array.from({ length: frameCount }, (_, index) =>
    index === frameCount - 1 ? DELAY.last : index === 0 ? DELAY.first : DELAY.ply,
  );
}

/**
 * Which side sits at the bottom.
 *
 * White, as every spectator sees the page — except where a person played black against a machine.
 * That person is the one most likely to be exporting it, and their own page put their pieces at the
 * bottom (`LiveGame` orients to the seat), so the GIF should look like the game they played.
 */
export function gifOrientation(game: Pick<GameDetail, "players">): Colour {
  const human = (colour: Colour) =>
    game.players.some((player) => player.colour === colour && player.kind === "human");
  return human("black") && !human("white") ? "black" : "white";
}

export interface SeatCaption {
  name: string;
  /** `1`, `0` or `½` once the game is decided; null while it is not. */
  score: string | null;
  /** How it ended, shown in one seat's strip only — see `gifCaptions`. */
  ending: string | null;
}

/**
 * The text in the two strips: each player's name, and on the final frame, the result.
 *
 * **The ending is stated once, not in both strips.** It goes beside the winner's score, where it
 * reads as "won, by checkmate"; a draw has no winner, so it goes to white's strip. A game with no
 * result — still running, or aborted — shows no score at all, because a `*` beside a name reads as
 * a footnote rather than as "undecided". An aborted game still says why it stopped.
 */
export function gifCaptions(
  game: Pick<GameDetail, "players" | "result" | "termination">,
): Record<Colour, SeatCaption> {
  const name = (colour: Colour) =>
    game.players.find((player) => player.colour === colour)?.display_name ??
    (colour === "white" ? "White" : "Black");

  const scores: Record<string, [string, string]> = {
    "1-0": ["1", "0"],
    "0-1": ["0", "1"],
    "1/2-1/2": ["½", "½"],
  };
  const decided = scores[game.result];
  const ending = game.termination ? endingLabel(game.termination) : null;
  const endingSeat: Colour = game.result === "0-1" ? "black" : "white";

  return {
    white: {
      name: name("white"),
      score: decided?.[0] ?? null,
      ending: endingSeat === "white" ? ending : null,
    },
    black: {
      name: name("black"),
      score: decided?.[1] ?? null,
      ending: endingSeat === "black" ? ending : null,
    },
  };
}

/**
 * `chessmark-gpt-5-vs-claude-opus-4-1-3f9a2c1b.gif`.
 *
 * The names make a downloads folder legible; the id prefix keeps two games between the same
 * pair from overwriting each other.
 */
export function gifFilename(game: Pick<GameDetail, "id" | "players">): string {
  const slug = (text: string) =>
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40)
      .replace(/-+$/g, "");
  const captions = gifCaptions({ ...game, result: "*", termination: null });
  const pair = [slug(captions.white.name), "vs", slug(captions.black.name)]
    .filter(Boolean)
    .join("-");
  return `chessmark-${pair}-${game.id.slice(0, 8)}.gif`;
}

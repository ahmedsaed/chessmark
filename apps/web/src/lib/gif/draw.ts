/**
 * A game, drawn a ply at a time and encoded as a GIF.
 *
 * **Free of the DOM**, so the same code runs on an `OffscreenCanvas` in `gif.worker.ts` and, where
 * a browser has no such thing, on an ordinary canvas in the page (`export.ts`). The pieces arrive
 * already rasterised, because decoding an SVG needs `Image`, and a worker has none.
 *
 * Covered by the browser suite rather than the unit suite: there is no canvas in Node, and a
 * mocked one would be asserting the mock. `frames.ts` holds the rules and is unit-tested.
 */

import { pieceKey } from "@/lib/captures";
import { encodeGif } from "@/lib/gif/encode";
import {
  gifCaptions,
  gifDelays,
  gifFrames,
  gifOrientation,
  type GifFrame,
  type SeatCaption,
} from "@/lib/gif/frames";
import { ranks } from "@/lib/og/fen";
import type { Colour, GameDetail } from "@/lib/types";

/* The design system's values (`globals.css`). Literal because a canvas has no cascade — the same
   reason `og/theme.ts` gives. */
const COLOUR = {
  surface: "#1e1a14",
  line: "#3a3227",
  ink: "#efe8da",
  inkDim: "#a99e8b",
  accent: "#dca84b",
  squareLight: "#9c8869",
  squareDark: "#4b3f2f",
  pieceWhite: "#f5efe2",
  pieceBlack: "#14110c",
} as const;

/* `--color-sq-mark` at 45%, as `Board` highlights the last move — pre-mixed into each square's
   colour so the GIF's palette holds two flat colours rather than a blend. */
const HIGHLIGHT = {
  light: mix(COLOUR.squareLight, "#c9a24c", 0.45),
  dark: mix(COLOUR.squareDark, "#c9a24c", 0.45),
};

const FONT_MONO =
  'ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Menlo, Consolas, monospace';

const SQUARE = 60;
const BOARD = SQUARE * 8;
const STRIP = 40;
const PAD = 12;
export const GIF_SIZE = { width: BOARD, height: BOARD + STRIP * 2 } as const;

/** Each piece is drawn this size, inset from its square as `og/board.tsx` insets it. */
const INSET = Math.round(SQUARE * 0.07);
export const PIECE_SIZE = SQUARE - INSET * 2;

export type GifGame = Pick<GameDetail, "start_fen" | "moves" | "players" | "result" | "termination">;

/** Keyed `wK`, `bN` — `pieceKey`'s form — and rasterised at `PIECE_SIZE`. */
export type Pieces = Map<string, CanvasImageSource>;

type Context = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

export function renderGif(
  game: GifGame,
  pieces: Pieces,
  canvas: HTMLCanvasElement | OffscreenCanvas,
): Uint8Array {
  const frames = gifFrames(game.start_fen, game.moves);
  if (frames.length === 0) throw new Error("this game's start position could not be read");

  const orientation = gifOrientation(game);
  const captions = gifCaptions(game);

  canvas.width = GIF_SIZE.width;
  canvas.height = GIF_SIZE.height;
  /* `willReadFrequently` because every frame is read straight back out — without it the browser
     keeps the canvas on the GPU and each `getImageData` is a readback stall. */
  const context = canvas.getContext("2d", { willReadFrequently: true }) as Context | null;
  if (!context) throw new Error("this browser would not give a 2D canvas");

  const delays = gifDelays(frames.length);
  const rgba = frames.map((frame, index) => {
    drawFrame(context, frame, pieces, orientation, captions, index === frames.length - 1);
    return {
      rgba: context.getImageData(0, 0, GIF_SIZE.width, GIF_SIZE.height).data,
      delay: delays[index]!,
    };
  });

  return encodeGif(rgba, GIF_SIZE.width, GIF_SIZE.height, [rgba[0]!.rgba, rgba.at(-1)!.rgba]);
}

function drawFrame(
  context: Context,
  frame: GifFrame,
  pieces: Pieces,
  orientation: Colour,
  captions: Record<Colour, SeatCaption>,
  final: boolean,
) {
  const top: Colour = orientation === "white" ? "black" : "white";
  const bottom: Colour = orientation;

  drawStrip(context, 0, top, captions[top], final);
  drawStrip(context, STRIP + BOARD, bottom, captions[bottom], final);

  const flipped = orientation === "black";
  const marked = new Set(frame.lastMove ? [frame.lastMove.from, frame.lastMove.to] : []);

  ranks(frame.fen).forEach((rank, rankIndex) => {
    rank.forEach((piece, fileIndex) => {
      const square = `${"abcdefgh"[fileIndex]}${8 - rankIndex}`;
      const column = flipped ? 7 - fileIndex : fileIndex;
      const row = flipped ? 7 - rankIndex : rankIndex;
      const x = column * SQUARE;
      const y = STRIP + row * SQUARE;
      const light = (rankIndex + fileIndex) % 2 === 0;

      context.fillStyle = marked.has(square)
        ? light
          ? HIGHLIGHT.light
          : HIGHLIGHT.dark
        : light
          ? COLOUR.squareLight
          : COLOUR.squareDark;
      context.fillRect(x, y, SQUARE, SQUARE);

      const image = piece && pieces.get(pieceKey(piece.piece, piece.white ? "white" : "black"));
      if (image) context.drawImage(image, x + INSET, y + INSET, PIECE_SIZE, PIECE_SIZE);
    });
  });
}

/**
 * A player's strip: a chip in their colour, their name, and on the final frame the result.
 *
 * **The name gives way to the result, never the other way round.** Production names run long
 * ("Qwen3 235B A22B Thinking 2507 (free)"), and the score is the part a GIF is shared for, so the
 * right side is measured first and the name is cut to whatever is left.
 */
function drawStrip(
  context: Context,
  y: number,
  colour: Colour,
  caption: SeatCaption,
  final: boolean,
) {
  context.fillStyle = COLOUR.surface;
  context.fillRect(0, y, GIF_SIZE.width, STRIP);
  context.fillStyle = COLOUR.line;
  context.fillRect(0, y === 0 ? STRIP - 1 : y, GIF_SIZE.width, 1);

  const middle = y + STRIP / 2;
  const chip = 12;
  context.fillStyle = colour === "white" ? COLOUR.pieceWhite : COLOUR.pieceBlack;
  context.strokeStyle = COLOUR.inkDim;
  context.lineWidth = 1;
  context.fillRect(PAD, middle - chip / 2, chip, chip);
  context.strokeRect(PAD + 0.5, middle - chip / 2 + 0.5, chip - 1, chip - 1);

  context.textBaseline = "middle";
  let right = GIF_SIZE.width - PAD;

  if (final && caption.score) {
    context.font = `700 18px ${FONT_MONO}`;
    context.textAlign = "right";
    context.fillStyle = caption.score === "0" ? COLOUR.inkDim : COLOUR.accent;
    context.fillText(caption.score, right, middle);
    right -= context.measureText(caption.score).width + 10;
  }
  if (final && caption.ending) {
    context.font = `400 12px ${FONT_MONO}`;
    context.textAlign = "right";
    context.fillStyle = COLOUR.inkDim;
    const ending = caption.ending.toUpperCase();
    context.fillText(ending, right, middle);
    right -= context.measureText(ending).width + 14;
  }

  context.font = `600 14px ${FONT_MONO}`;
  context.textAlign = "left";
  context.fillStyle = COLOUR.ink;
  const left = PAD + chip + 10;
  context.fillText(fit(context, caption.name, right - left), left, middle);
}

/** The text, cut with an ellipsis until it fits `width`. */
function fit(context: Context, text: string, width: number): string {
  if (context.measureText(text).width <= width) return text;
  let end = text.length;
  while (end > 0 && context.measureText(`${text.slice(0, end).trimEnd()}…`).width > width) end -= 1;
  return end > 0 ? `${text.slice(0, end).trimEnd()}…` : "";
}

function mix(base: string, over: string, amount: number): string {
  const channel = (hex: string, index: number) => parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16);
  const mixed = [0, 1, 2].map((index) =>
    Math.round(channel(base, index) * (1 - amount) + channel(over, index) * amount),
  );
  return `#${mixed.map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

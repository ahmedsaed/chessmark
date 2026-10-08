/**
 * Draws and encodes a game's GIF off the main thread.
 *
 * **A long game is hundreds of frames, and the page must stay usable while they are made.** On the
 * main thread a 447-ply game froze the page for about 1.6 s: the scrubber, the event stream and
 * the button itself all stopped answering. Here the same `renderGif` runs on an `OffscreenCanvas`,
 * and the page only waits for a message.
 *
 * Started by `export.ts` with `new Worker(new URL(...))`, which the bundler turns into a chunk of
 * its own. It is fetched only on that first export, never as part of the page.
 */

import { renderGif, type GifGame } from "@/lib/gif/draw";

export interface GifRequest {
  game: GifGame;
  pieces: Record<string, ImageBitmap>;
}

export type GifResponse = { bytes: Uint8Array } | { error: string };

/* The project compiles against the DOM lib, not the worker one, so the two members used here are
   declared rather than pulling `webworker` types into every file. */
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<GifRequest>) => void) | null;
  postMessage(message: GifResponse, transfer?: Transferable[]): void;
};

scope.onmessage = ({ data }) => {
  try {
    // Sized by `renderGif`, which owns the dimensions.
    const bytes = renderGif(data.game, new Map(Object.entries(data.pieces)), new OffscreenCanvas(1, 1));
    /* Copied to a buffer of exactly its length, then transferred rather than cloned: `bytes()` is a
       view over gifenc's growable buffer, and a 1 MB GIF should move, not be copied twice. */
    const exact = bytes.slice();
    scope.postMessage({ bytes: exact }, [exact.buffer]);
  } catch (error) {
    scope.postMessage({ error: error instanceof Error ? error.message : String(error) });
  }
};

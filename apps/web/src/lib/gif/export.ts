/**
 * A game's GIF, made off the main thread where the browser allows it.
 *
 * The entry point `GameActions` imports when the button is pressed, never before, so neither this,
 * the worker nor `gifenc` is in the game page's bundle (ADR-0065).
 *
 * The pieces are rasterised here, because decoding an SVG needs `Image` and a worker has none.
 * That is twelve small decodes; everything that scales with the game — every frame, the encode —
 * happens in `gif.worker.ts`.
 */

import { PIECE_SIZE, renderGif, type GifGame } from "@/lib/gif/draw";
import type { GifRequest, GifResponse } from "@/lib/gif/gif.worker";
import { PIECE_SVG } from "@/lib/og/pieces";

export async function gameGif(game: GifGame): Promise<Blob> {
  const images = await loadPieces();
  const bitmaps = await toBitmaps(images);

  const bytes =
    bitmaps && typeof Worker !== "undefined" && typeof OffscreenCanvas !== "undefined"
      ? await inWorker(game, bitmaps)
      : /* A browser without `OffscreenCanvas` (Safari before 16.4) still gets its GIF, the way
           the first version made it: in the page, which pauses while it works. */
        renderGif(game, images, document.createElement("canvas")).slice();

  return new Blob([bytes as Uint8Array<ArrayBuffer>], { type: "image/gif" });
}

function inWorker(game: GifGame, pieces: Map<string, ImageBitmap>): Promise<Uint8Array> {
  const worker = new Worker(new URL("./gif.worker.ts", import.meta.url), { type: "module" });

  return new Promise<Uint8Array>((resolve, reject) => {
    worker.onmessage = ({ data }: MessageEvent<GifResponse>) => {
      if ("bytes" in data) resolve(data.bytes);
      else reject(new Error(data.error));
    };
    worker.onerror = (event) => reject(new Error(event.message || "the GIF worker failed"));

    /* `game` is a plain record and is cloned; the bitmaps are transferred, which leaves them
       unusable here — they are made fresh for each export, so nothing else holds them. */
    const request: GifRequest = { game, pieces: Object.fromEntries(pieces) };
    worker.postMessage(request, [...pieces.values()]);
  }).finally(() => worker.terminate());
}

/** The twelve pieces as decoded images, at the size they are drawn. */
async function loadPieces(): Promise<Map<string, HTMLImageElement>> {
  const keys = ["w", "b"].flatMap((side) => ["K", "Q", "R", "B", "N", "P"].map((p) => side + p));
  const entries = await Promise.all(
    keys.map(async (key) => {
      /* Given a size in the markup itself. The vendored SVGs say `width="100%"`, which leaves an
         image with no intrinsic size, and a canvas cannot reliably draw one of those — Firefox
         refuses outright. Sized to exactly what is drawn, so the bitmap is never rescaled. */
      const svg = PIECE_SVG[key]!.replace(
        'width="100%" height="100%"',
        `width="${PIECE_SIZE}" height="${PIECE_SIZE}"`,
      );
      const image = new Image();
      image.src = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
      await image.decode();
      return [key, image] as const;
    }),
  );
  return new Map(entries);
}

/**
 * The same pieces as `ImageBitmap`s, which a worker can be handed. Null where the browser will
 * not make a bitmap from an SVG image, which sends the export down the in-page path instead.
 */
async function toBitmaps(
  images: Map<string, HTMLImageElement>,
): Promise<Map<string, ImageBitmap> | null> {
  if (typeof createImageBitmap === "undefined") return null;
  try {
    const entries = await Promise.all(
      [...images].map(async ([key, image]) => [key, await createImageBitmap(image)] as const),
    );
    return new Map(entries);
  } catch {
    return null;
  }
}

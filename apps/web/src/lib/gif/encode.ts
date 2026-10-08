/**
 * RGBA frames to GIF bytes.
 *
 * Kept free of the DOM so the unit suite can run it on synthetic pixels and read the result back:
 * "it produced bytes" is not a test, "it produced one image per ply with these delays" is.
 */

import { GIFEncoder, applyPalette, quantize } from "gifenc";

export interface RgbaFrame {
  rgba: Uint8ClampedArray;
  delay: number;
}

/**
 * One palette for the whole GIF, quantised from a sample of frames.
 *
 * **Shared, not per frame.** A palette quantised per frame shifts slightly every time a piece
 * moves, and the board's flat squares visibly shimmer between two near-identical browns. The
 * sample should contain every colour the GIF will use — the opening frame has every piece, the
 * final frame has the last-move highlight and the result text.
 */
export function encodeGif(
  frames: RgbaFrame[],
  width: number,
  height: number,
  sample: Uint8ClampedArray[],
): Uint8Array {
  const pooled = new Uint8ClampedArray(sample.reduce((total, rgba) => total + rgba.length, 0));
  let offset = 0;
  for (const rgba of sample) {
    pooled.set(rgba, offset);
    offset += rgba.length;
  }
  /* 255 colours, and the 256th slot left for "unchanged" — see below.

     **Pixels are mapped against the 255, never against the table with the extra slot in it.**
     The slot needs *some* colour, and whatever it is, `applyPalette` will happily pick it as the
     nearest match for a real pixel. It was black the first time, and every black pixel after the
     first frame became transparent: black pieces faded into the square they had moved to. */
  const colours = quantize(pooled, 255);
  const unchanged = colours.length;
  const palette = [...colours, [0, 0, 0]];

  const gif = GIFEncoder();
  let previous: Uint8Array | null = null;
  frames.forEach((frame, index) => {
    const indexed = applyPalette(frame.rgba, colours);

    /* **Only what moved is written.** A ply changes two to four squares of a 480×560 frame, and
       writing the whole frame every time made a 447-ply game a 6.2 MB file. Every pixel that
       matches the previous frame becomes the transparent index, which LZW compresses to almost
       nothing, and disposal 1 ("leave it") keeps the previous frame showing through. Compared
       after palette mapping, so "matches" is exact rather than a colour-distance guess. */
    let pixels = indexed;
    if (previous) {
      pixels = indexed.slice();
      for (let i = 0; i < pixels.length; i += 1) {
        if (pixels[i] === previous[i]) pixels[i] = unchanged;
      }
    }

    gif.writeFrame(pixels, width, height, {
      // The palette goes on the first frame as the global table; later frames reuse it.
      palette: index === 0 ? palette : undefined,
      delay: frame.delay,
      // 0 is "loop forever", and it only means anything on the first frame.
      repeat: index === 0 ? 0 : undefined,
      transparent: previous !== null,
      transparentIndex: unchanged,
      dispose: 1,
    });
    previous = indexed;
  });
  gif.finish();
  return gif.bytes();
}

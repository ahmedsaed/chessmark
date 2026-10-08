/**
 * Reads a GIF back: its size, each image's delay, and what a viewer actually sees on each frame.
 *
 * For tests, in both suites. A GIF export that is `200 · image/gif · > 5 KB` could be a single
 * frame of the opening position; this walks the blocks so a test can assert one image per ply and
 * the hold on the last one.
 *
 * Walks the block structure rather than searching for the graphic-control marker bytes, which can
 * occur by chance inside compressed image data.
 */

export interface GifStructure {
  width: number;
  height: number;
  /** Per image, in milliseconds. */
  delays: number[];
  loops: boolean;
  /**
   * Each frame as displayed — composited over the frames before it, honouring transparency.
   *
   * The export writes only the pixels that changed, so a frame's own image data is mostly holes.
   * What a test must compare is the picture a viewer gets, and that only exists after compositing.
   */
  screens: Uint8ClampedArray[];
}

export function readGif(bytes: Uint8Array): GifStructure {
  const ascii = String.fromCharCode(...bytes.slice(0, 6));
  if (ascii !== "GIF89a" && ascii !== "GIF87a") throw new Error(`not a GIF: ${ascii}`);

  const u16 = (at: number) => bytes[at]! | (bytes[at + 1]! << 8);
  const width = u16(6);
  const height = u16(8);
  let at = 13;
  const flags = bytes[10]!;
  let globalTable: Uint8Array | null = null;
  if (flags & 0x80) {
    const size = 3 * (1 << ((flags & 0x07) + 1));
    globalTable = bytes.slice(at, at + size);
    at += size;
  }

  const readSubBlocks = () => {
    const chunks: number[] = [];
    while (bytes[at] !== 0) {
      chunks.push(...bytes.slice(at + 1, at + 1 + bytes[at]!));
      at += bytes[at]! + 1;
    }
    at += 1;
    return chunks;
  };

  const delays: number[] = [];
  const screens: Uint8ClampedArray[] = [];
  const screen = new Uint8ClampedArray(width * height * 4);
  let pendingDelay = 0;
  let transparent: number | null = null;
  let loops = false;

  while (at < bytes.length) {
    const block = bytes[at]!;
    if (block === 0x3b) break;
    if (block === 0x21) {
      const label = bytes[at + 1]!;
      if (label === 0xf9) {
        pendingDelay = u16(at + 4) * 10;
        transparent = bytes[at + 3]! & 1 ? bytes[at + 6]! : null;
      }
      if (label === 0xff && String.fromCharCode(...bytes.slice(at + 3, at + 14)) === "NETSCAPE2.0")
        loops = true;
      at += 2;
      readSubBlocks();
    } else if (block === 0x2c) {
      const [left, top, w, h] = [u16(at + 1), u16(at + 3), u16(at + 5), u16(at + 7)];
      const local = bytes[at + 9]!;
      at += 10;
      let table = globalTable;
      if (local & 0x80) {
        const size = 3 * (1 << ((local & 0x07) + 1));
        table = bytes.slice(at, at + size);
        at += size;
      }
      const minimum = bytes[at]!;
      at += 1;
      const indices = lzwDecode(readSubBlocks(), minimum, w * h);

      /* Disposal is not modelled: the encoder writes "leave in place" on every frame, so each
         one is drawn straight over the last. */
      for (let i = 0; i < indices.length; i += 1) {
        const index = indices[i]!;
        if (index === transparent || !table) continue;
        const x = left + (i % w);
        const y = top + Math.floor(i / w);
        screen.set([table[index * 3]!, table[index * 3 + 1]!, table[index * 3 + 2]!, 255], (y * width + x) * 4);
      }
      screens.push(screen.slice());
      delays.push(pendingDelay);
      pendingDelay = 0;
      transparent = null;
    } else {
      throw new Error(`unexpected block 0x${block.toString(16)} at ${at}`);
    }
  }

  return { width, height, delays, loops, screens };
}

/** GIF's variable-width LZW, decoded to colour indices. */
function lzwDecode(data: number[], minimum: number, count: number): Uint8Array {
  const clear = 1 << minimum;
  const end = clear + 1;
  const out = new Uint8Array(count);
  let written = 0;

  let size = minimum + 1;
  let dictionary: number[][] = [];
  const reset = () => {
    dictionary = Array.from({ length: clear + 2 }, (_, i) => [i]);
    size = minimum + 1;
  };
  reset();

  let bit = 0;
  let previous: number[] | null = null;
  while (written < count) {
    let code = 0;
    for (let i = 0; i < size; i += 1, bit += 1) {
      if ((data[bit >> 3]! >> (bit & 7)) & 1) code |= 1 << i;
    }
    if (code === clear) {
      reset();
      previous = null;
      continue;
    }
    if (code === end) break;

    let entry: number[];
    if (code < dictionary.length) entry = dictionary[code]!;
    else if (previous) entry = [...previous, previous[0]!];
    else throw new Error(`bad LZW code ${code}`);

    for (const index of entry) if (written < count) out[written++] = index;
    if (previous && dictionary.length < 4096) dictionary.push([...previous, entry[0]!]);
    if (dictionary.length === 1 << size && size < 12) size += 1;
    previous = entry;
  }
  return out;
}

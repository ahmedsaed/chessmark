import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

import { fixtures } from "../fixtures";
import { readGif } from "../../src/lib/__fixtures__/gif";

/**
 * Exporting a game as a GIF (`lib/gif`).
 *
 * The rules — one frame per ply, the holds, the captions — are unit-tested. This is the part only a
 * browser can show: that the button, the canvas and the encoder together produce a GIF of *this*
 * game. So the download is read back and composited frame by frame, and the final picture is
 * checked square by square against Scholar's Mate — a GIF of the opening position repeated eight
 * times would be a valid, looping, correctly sized image and would pass anything short of that.
 */

/* `lib/gif/draw.ts`: 60px squares under a 40px strip, white at the bottom. */
const SQUARE = 60;
const STRIP = 40;

function squareAt(screen: Uint8ClampedArray, width: number, name: string) {
  const file = name.charCodeAt(0) - 97;
  const rank = Number(name[1]);
  const left = file * SQUARE;
  const top = STRIP + (8 - rank) * SQUARE;
  const pixels: [number, number, number][] = [];
  for (let y = top; y < top + SQUARE; y += 1) {
    for (let x = left; x < left + SQUARE; x += 1) {
      const at = (y * width + x) * 4;
      pixels.push([screen[at]!, screen[at + 1]!, screen[at + 2]!]);
    }
  }
  return pixels;
}

const near = (a: number[], b: number[]) => a.every((value, i) => Math.abs(value - b[i]!) <= 12);

/**
 * A square's piece as a drawing: which of its pixels are pure ink, white or black.
 *
 * Compared drawing to drawing rather than classified by colour, because the Cburnett set
 * (`og/pieces.ts`) uses both inks on both sides — the white queen is more black outline than white
 * fill. "The piece on f7 at the end is the drawing that stood on d1 at the start" is the claim, and
 * it holds whatever the square underneath, since only pure ink is compared.
 */
function ink(pixels: number[][]): string[] {
  return pixels.map((p) => (near(p, [255, 255, 255]) ? "w" : near(p, [0, 0, 0]) ? "b" : "."));
}

const isEmpty = (drawing: string[]) => drawing.filter((c) => c !== ".").length < 50;

/** Edges are blended into the square's colour, so a handful of pixels differ between squares. */
function sameDrawing(a: string[], b: string[]): boolean {
  const differing = a.filter((c, i) => c !== b[i]).length;
  return !isEmpty(a) && differing / a.length < 0.03;
}

test("the GIF is the game, one frame per ply, ending on the mate", async ({ page }) => {
  await page.goto(`/games/${fixtures().replayGame}`);

  /* Off the main thread: a long game is hundreds of frames, and drawing them in the page froze it
     for over a second (ADR-0065). The claim is that pressing the button starts a worker on a page
     that had none — not the worker's URL, which names the GIF in a dev build and is a generic
     Turbopack bootstrap in a production one, the first version of this line's failure in CI. */
  expect(page.workers(), "the game page should run no worker before the export").toEqual([]);
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.waitForEvent("worker"),
    page.getByRole("button", { name: "Export GIF" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^chessmark-.+-vs-.+-[0-9a-f]{8}\.gif$/);

  const gif = readGif(new Uint8Array(await readFile((await download.path())!)));
  expect([gif.width, gif.height]).toEqual([480, 560]);
  expect(gif.loops).toBe(true);
  // Seven plies of Scholar's Mate, plus the position before the first.
  expect(gif.delays).toEqual([1000, 800, 800, 800, 800, 800, 800, 3000]);

  const first = gif.screens[0]!;
  const last = gif.screens.at(-1)!;

  const on = (screen: Uint8ClampedArray, square: string) =>
    ink(squareAt(screen, gif.width, square));

  // The opening frame is the opening: c6 is empty, f7 holds a pawn rather than a queen.
  expect(isEmpty(on(first, "c6"))).toBe(true);
  expect(sameDrawing(on(first, "f7"), on(first, "d1"))).toBe(false);

  // The last frame is the mate: the queen has left d1 and h5 and stands on f7, and the knights
  // are on c6 and f6 — black pieces that only arrive in later frames, which is exactly what the
  // changed-pixels-only encoding once dropped.
  expect(isEmpty(on(last, "d1"))).toBe(true);
  expect(isEmpty(on(last, "h5"))).toBe(true);
  expect(sameDrawing(on(last, "f7"), on(first, "d1"))).toBe(true);
  expect(sameDrawing(on(last, "c6"), on(first, "b8"))).toBe(true);
  expect(sameDrawing(on(last, "f6"), on(first, "g8"))).toBe(true);
  expect(sameDrawing(on(last, "e8"), on(first, "e8"))).toBe(true);

  await expect(page.getByRole("status").filter({ hasText: "GIF downloaded" })).toBeAttached();
});

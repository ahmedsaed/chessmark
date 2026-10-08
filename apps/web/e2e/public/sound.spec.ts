import { expect, test, type Page } from "@playwright/test";

import { fixtures } from "../fixtures";

/**
 * Move sounds on the replay (`lib/sound.ts`, `hooks/useMoveSounds.ts`).
 *
 * The rules of *which* sound are unit-tested; this is the part only a browser can show — that the
 * files are fetched, decoded after a gesture and actually started, that a jump stays silent, and
 * that turning sound off survives a reload. Nothing here listens: every buffer the page starts is
 * recorded by name instead, which is the claim ("this step played the capture") rather than the
 * plumbing ("an AudioContext exists").
 *
 * The game is Scholar's Mate: six quiet moves, then `Qxf7#`.
 */

/**
 * Records the name of every sound the page starts.
 *
 * Buffers are named by content, not by size: two of the files are the same number of bytes, and a
 * map keyed on length silently reported every "move" as a "promote".
 */
async function recordSounds(page: Page) {
  await page.addInitScript(() => {
    const names = new Map<string, string>();
    const signature = (bytes: ArrayBuffer) => {
      const view = new Uint8Array(bytes);
      const middle = Math.floor(view.length / 2);
      return `${view.length}:${view.slice(middle, middle + 64).join(",")}`;
    };
    const played: string[] = [];
    (window as unknown as { __played: string[] }).__played = played;

    const fetchOriginal = window.fetch;
    window.fetch = async (...args) => {
      const response = await fetchOriginal(...args);
      const url = String(args[0]);
      if (url.includes("/sounds/")) {
        names.set(signature(await response.clone().arrayBuffer()), url.split("/").pop()!);
      }
      return response;
    };

    const decode = AudioContext.prototype.decodeAudioData;
    AudioContext.prototype.decodeAudioData = async function (this: AudioContext, bytes) {
      const name = names.get(signature(bytes));
      const buffer = await decode.call(this, bytes);
      (buffer as AudioBuffer & { __name?: string }).__name = name;
      return buffer;
    } as typeof decode;

    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (this: AudioBufferSourceNode, ...args) {
      played.push((this.buffer as AudioBuffer & { __name?: string }).__name ?? "?");
      return start.apply(this, args);
    };
  });
}

const played = (page: Page) =>
  page.evaluate(() => (window as unknown as { __played: string[] }).__played.slice());

/* Named for what it controls, with `aria-pressed` carrying the state — the name no longer changes
   when it is pressed, which is how a toggle is meant to read to a screen reader. */
const soundToggle = (page: Page) => page.getByRole("button", { name: "Move sounds" });

test.beforeEach(async ({ page }) => {
  await recordSounds(page);
  await page.goto(`/games/${fixtures().replayGame}`);
  // The files are fetched once the page is idle; wait for them rather than for a fixed time.
  await page.waitForFunction(
    () =>
      performance.getEntriesByType("resource").filter((e) => e.name.includes("/sounds/"))
        .length === 6,
  );
});

test("stepping through a replay plays each move, and the mate as the game's end", async ({
  page,
}) => {
  await expect(soundToggle(page)).toHaveAttribute("aria-pressed", "true");

  // Opening at the final position is not a move arriving, and neither is rewinding to the start.
  await page.getByRole("button", { name: "Start" }).click();
  expect(await played(page)).toEqual([]);

  const next = page.getByRole("button", { name: "Next ply" });
  for (let ply = 1; ply <= 7; ply += 1) {
    await next.click();
    await expect.poll(() => played(page).then((names) => names.length)).toBe(ply);
  }

  expect(await played(page)).toEqual([
    ...Array(6).fill("move.mp3"),
    // `Qxf7#` is a capture, a check and the end of the game; it is heard as the end.
    "game-end.mp3",
  ]);

  // A jump is silent in both directions.
  await page.getByRole("button", { name: "Start" }).click();
  await page.getByRole("button", { name: "End" }).click();
  await page.waitForTimeout(300);
  expect(await played(page)).toHaveLength(7);
});

test("turning sound off silences the board and survives a reload", async ({ page }) => {
  await soundToggle(page).click();
  await expect(soundToggle(page)).toHaveAttribute("aria-pressed", "false");
  await expect(soundToggle(page)).toHaveAttribute("title", "Sound off");

  await page.getByRole("button", { name: "Start" }).click();
  await page.getByRole("button", { name: "Next ply" }).click();
  await page.waitForTimeout(300);
  expect(await played(page)).toEqual([]);

  await page.reload();
  await expect(soundToggle(page)).toHaveAttribute("aria-pressed", "false");

  // And back on: the click that turns it on is itself the gesture that unlocks audio.
  await soundToggle(page).click();
  await page.getByRole("button", { name: "Start" }).click();
  await page.getByRole("button", { name: "Next ply" }).click();
  await expect.poll(() => played(page)).toEqual(["move.mp3"]);
});

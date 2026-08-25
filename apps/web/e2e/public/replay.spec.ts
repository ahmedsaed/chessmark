import { expect, test } from "@playwright/test";

import { fixtures } from "../fixtures";

/**
 * Replay (Phase 8, LOG-07, ADR-0008).
 *
 * A finished game is scrubbable ply by ply and the raw provider payload behind every turn is one
 * click away — invariant 3, the promise that any number on the leaderboard has its transcript
 * reachable. Replay truncates the same event log the live view reads, so an assertion here covers
 * both.
 *
 * The game is Scholar's Mate, played into the database by the seed project through the real queue
 * and worker.
 */

test.beforeEach(async ({ page }) => {
  await page.goto(`/games/${fixtures().replayGame}`);
});

test("a finished game opens at the final position", async ({ page }) => {
  const board = page.locator("[data-fen]").first();
  await expect(board).toBeVisible();

  // Scholar's Mate: White's queen takes f7 and the black king has nowhere. The FEN is asserted
  // whole, because "a board is visible" would pass for any position at all.
  await expect(board).toHaveAttribute(
    "data-fen",
    "r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 4",
  );
});

test("the scrubber steps ply by ply, forwards and back", async ({ page }) => {
  const board = page.locator("[data-fen]").first();
  const start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(board).toHaveAttribute("data-fen", start);

  // One ply forward is 1. e4 — a different position, not merely a re-render.
  await page.getByRole("button", { name: "Next ply" }).click();
  await expect(board).toHaveAttribute("data-fen", /^rnbqkbnr\/pppppppp\/8\/8\/4P3/);

  await page.getByRole("button", { name: "Previous ply" }).click();
  await expect(board).toHaveAttribute("data-fen", start);
});

test("the slider seeks to an arbitrary ply", async ({ page }) => {
  // `getByLabel` is ambiguous here: the step buttons are labelled "Previous ply"/"Next ply".
  const slider = page.getByRole("slider", { name: "Ply" });
  const board = page.locator("[data-fen]").first();
  await expect(slider).toHaveValue("7");

  await slider.fill("3");

  await expect(slider).toHaveAttribute("aria-valuetext", "ply 3 of 7");
  // 1. e4 e5 2. Bc4 — the bishop is on c4 and it is Black to move.
  await expect(board).toHaveAttribute("data-fen", /2B1P3.* b /);
});

test("the keyboard drives the scrubber", async ({ page }) => {
  const board = page.locator("[data-fen]").first();

  await page.keyboard.press("Home");
  await expect(board).toHaveAttribute(
    "data-fen",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  );

  await page.keyboard.press("End");
  await expect(board).toHaveAttribute("data-fen", /Q/);
});

test("finished turns are folded, and expanding one reveals its raw payload", async ({ page }) => {
  // The fold is the live view's, reused (ADR-0008) — finished turns collapse to a tool count.
  // `aria-expanded`, not the ▸ glyph — that glyph is `aria-hidden`, so it is not part of the
  // button's accessible name and a person using a screen reader never meets it. Filtered by text
  // as well, because the account button in the header is a collapsed disclosure too.
  const folded = page.locator('button[aria-expanded="false"]').filter({ hasText: /\d+ tools/ });
  await expect(folded.first()).toBeVisible();

  // A folded turn offers no `raw` link; only the open one does.
  const firstTurnRaw = page.getByRole("button", { name: "raw", exact: true });
  const openCount = await firstTurnRaw.count();

  await folded.first().click();
  await expect(page.getByRole("button", { name: "raw", exact: true })).toHaveCount(openCount + 1);

  await page.getByRole("button", { name: "raw", exact: true }).first().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Raw transcript")).toBeVisible();

  // The numbers and the payload that produced them, side by side — the point of LOG-07. A cost
  // on a page must be traceable to the call log it came from.
  for (const stat of ["prompt", "cached", "output", "cost", "latency"]) {
    await expect(dialog.getByText(stat, { exact: true }).first()).toBeVisible();
  }

  // The response is open by default; the request is folded, because it is the enormous one.
  await dialog.getByRole("button", { name: /^request/ }).click();

  // Verbatim, not summarised (invariant 3): the message list actually sent, not a description
  // of it. The system prompt heading the transcript is the byte-stable prefix of ADR-0003.
  const request = dialog.locator("pre").first();
  await expect(request).toContainText('"messages"');
  await expect(request).toContainText("You are playing a game of chess");

  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toBeHidden();
});

test("reasoning is readable once the game is over (HUMAN-07, invariant 8)", async ({ page }) => {
  // The scripted Black side is given reasoning precisely so this is assertable. While a game is
  // live the API withholds it entirely (invariant 8); this game is finished, so it must be here.
  // Black's turns are the two-tool ones: get_board, then make_move.
  await page.locator('button[aria-expanded="false"]').filter({ hasText: "2 tools" }).first().click();

  await expect(page.getByText(/classical reply is e5/i)).toBeVisible();
});

test("the conversation can be filtered down to moves alone", async ({ page }) => {
  await expect(page.getByRole("button", { name: /\d+ tools/ }).first()).toBeVisible();

  await page.getByRole("button", { name: "Moves", exact: true }).click();

  // Talk is White's register in the scripted game; filtering to moves must drop it.
  await expect(page.getByText(/Mate on f7\. Good game\./)).toBeHidden();
});

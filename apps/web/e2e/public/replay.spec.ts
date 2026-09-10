import { expect, test, type Page } from "@playwright/test";

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

test("finished turns are folded, and each one links to its raw payload", async ({ page }) => {
  // The fold is the live view's, reused (ADR-0008) — finished turns collapse to their disclosures.
  // `aria-expanded`, not the ▸ glyph — that glyph is `aria-hidden`, so it is not part of the
  // button's accessible name and a person using a screen reader never meets it. Filtered by text
  // as well, because the account button in the header is a collapsed disclosure too.
  const folded = page.locator('button[aria-expanded="false"]').filter({ hasText: /\d+ tools/ });
  await expect(folded.first()).toBeVisible();

  // `raw` belongs to the turn rather than to a disclosure, so it is there while everything is
  // folded. It used to appear only once a turn was open, back when a turn had one fold to be in.
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

/**
 * Open one folded turn and return its unrolled container.
 *
 * The two sides of the scripted game are deliberately different shapes and the index matters:
 * **0 is White**, who writes prose and talks, and **1 is Black**, who reasons twice across two
 * provider rounds. Reading them by position rather than by content is what keeps these assertions
 * about the panel rather than about whichever turn happened to sort first.
 */
async function openTurn(page: Page, index: number) {
  const turn = page.getByTestId("turn").nth(index);
  const fold = turn.locator("button[aria-expanded]").filter({ hasText: /\d+ steps?/ }).first();
  /* The last turn of a finished game is already unrolled — it is the focused one — so opening
     unconditionally would close it. */
  if ((await fold.getAttribute("aria-expanded")) === "false") await fold.click();
  return turn.getByTestId("turn-steps");
}

/** The kinds of a turn's steps, in the order they are drawn. */
async function stepKinds(steps: ReturnType<typeof openTurn> extends Promise<infer T> ? T : never) {
  await steps.locator("[data-step]").first().waitFor();
  return steps
    .locator("[data-step]")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-step")));
}

test("reasoning is readable once the game is over (HUMAN-07, invariant 8)", async ({ page }) => {
  // The scripted Black side is given reasoning precisely so this is assertable. While a game is
  // live the API withholds it entirely (invariant 8); this game is finished, so it must be here.
  const steps = await openTurn(page, 1);
  await steps
    .locator('[data-step="reasoning"] button[aria-expanded="false"]')
    .first()
    .click();

  await expect(page.getByText(/classical reply is e5/i).first()).toBeVisible();
});

test("a turn renders in the order the model acted", async ({ page }) => {
  /**
   * **The guarantee this whole change exists for.** Black looks at the board, thinks about what
   * came back, and moves — two provider rounds — so the panel must read
   * `reasoning → tool → reasoning → tool`. It used to render by kind: every thought, then every
   * tool call, so the second thought appeared *above* the `get_board` it was about.
   *
   * Asserted on the rendered DOM rather than on the fold, because the fold is already covered by
   * unit tests and it is the screen that was wrong.
   */
  expect(await stepKinds(await openTurn(page, 1))).toEqual([
    "reasoning",
    "tool",
    "reasoning",
    "tool",
  ]);
});

test("a turn that talks draws the talk where it was said", async ({ page }) => {
  /* White's shape, and the other half of the same guarantee: prose, then the tools, with the
     message to the opponent between the `say` call that produced it and the move that followed.
     Every one of those used to be drawn in its own group — prose, then all three calls, then the
     message — so nothing on screen said the taunt came before the move. */
  expect(await stepKinds(await openTurn(page, 0))).toEqual([
    "output",
    "tool",
    "tool",
    "said",
    "tool",
  ]);
});

test("each reasoning block collapses on its own, body included", async ({ page }) => {
  /* Two blocks in one turn, and opening the first must leave the second closed — the per-kind
     toggles this replaced could only open both. The body is the other half: reading to the end of
     a long block and then scrolling back to its header to close it is the annoyance being fixed,
     so the text itself is the control. */
  const steps = await openTurn(page, 1);
  const blocks = steps.locator('[data-step="reasoning"] button[aria-expanded]');

  await expect(blocks).toHaveCount(2);
  await expect(blocks.nth(0)).toHaveAttribute("aria-expanded", "false");

  await blocks.nth(0).click();
  await expect(blocks.nth(0)).toHaveAttribute("aria-expanded", "true");
  await expect(blocks.nth(1)).toHaveAttribute("aria-expanded", "false");

  // Clicking the prose closes it again, without going near the header.
  await steps.getByRole("button", { name: /^Collapse / }).first().click();
  await expect(blocks.nth(0)).toHaveAttribute("aria-expanded", "false");
});

test("output is shown without being asked for", async ({ page }) => {
  /* White's scripted turn writes prose. It was closed by default everywhere, on the reasoning
     that it was "not worth being handed unasked" — and since it is a fraction as common as
     reasoning, a reader who had to ask for it never learned it was there. */
  await openTurn(page, 0);

  await expect(page.getByText(/I will play the Italian Game/i).first()).toBeVisible();
});

test("the conversation can be filtered down to moves alone", async ({ page }) => {
  await expect(page.getByRole("button", { name: /\d+ tools/ }).first()).toBeVisible();

  await page.getByRole("button", { name: "Moves", exact: true }).click();

  // Talk is White's register in the scripted game; filtering to moves must drop it.
  await expect(page.getByText(/Mate on f7\. Good game\./)).toBeHidden();
});

test("the newest turn is open and its reasoning is not", async ({ page }) => {
  /**
   * Two defaults, and they pull in opposite directions on purpose. The turn a reader is following
   * is the last one, so it opens without being asked — a live game spends most of its time
   * *between* turns, and `turn.live` alone left the panel a column of folded rows over a board
   * that had just moved. Inside it, reasoning stays shut: it is the longest and least
   * load-bearing thing in a turn, and opening the turn into a wall of it buries the tool call and
   * the move that a reader actually came for.
   */
  const last = page.getByTestId("turn").last();

  await expect(last.locator("button[aria-expanded]").first()).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(last.getByTestId("turn-steps")).toBeVisible();

  const reasoning = last.locator('[data-step="reasoning"] button[aria-expanded]');
  if (await reasoning.count()) {
    await expect(reasoning.first()).toHaveAttribute("aria-expanded", "false");
  }
});

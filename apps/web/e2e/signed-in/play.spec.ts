import { expect, test, type Page } from "@playwright/test";

/**
 * Human vs model, end to end (Phase 10, Phase 23 — HUMAN-01…HUMAN-07).
 *
 * The whole flow a person actually walks: sign in, pick an opponent, sit down, move a piece, watch
 * the model answer, reload, and resign. Every one of those was verified by hand in Phase 10 and by
 * nothing else — the duplicate-key bug in the conversation panel reached a real game before
 * anyone noticed.
 *
 * The session comes from `auth.setup.ts` — a real Clerk sign-in. Model turns come from
 * `scripts/worker.py --scripted`, started for the life of the suite: the real queue, the real turn
 * loop, the real costing, with only the provider replaced. Nothing here spends money.
 */

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

/**
 * White to move on move **two** — i.e. both sides have moved, so the model has replied.
 *
 * `/ w /` is not this signal, and using it cost a debugging pass: the starting position is also
 * white-to-move, so the assertion passed instantly and every later step read a board that had not
 * moved yet. A wait that is already satisfied is not a wait.
 */
const MODEL_HAS_REPLIED = / w \S+ \S+ \d+ 2$/;

/** What `scripts/worker.py --scripted` says it is thinking. Kept in step with SCRIPTED_REASONING. */
const SCRIPTED_REASONING = /Taking the first move the board offers/i;

/** Sit down against the first model the picker offers, as White. Returns the game's URL. */
async function sitDown(page: Page): Promise<string> {
  await page.goto("/play");

  // The trigger is labelled by its field label rather than by its own text, so it is addressed
  // by what it *is* — the combobox that opens the model listbox.
  await page.locator('button[aria-haspopup="listbox"]').first().click();

  // Typing auto-expands every matching provider, so the options are reachable without also
  // clicking the provider row open.
  await page.getByPlaceholder("Search models or providers…").fill("gemini");
  await page.getByRole("option").first().click();

  await page.getByRole("button", { name: "white", exact: true }).click();
  await page.getByRole("button", { name: "sit down" }).click();

  await page.waitForURL(/\/games\/[0-9a-f-]{36}/);
  return page.url();
}

/**
 * A folded turn in the conversation.
 *
 * Scoped by its text, not by `aria-expanded` alone: the account button in the site header is also
 * a collapsed disclosure, and it sorts first in the document. Clicking *that* opens the Clerk user
 * menu over the page, and every later click then fails on an element it has covered.
 */
function foldedTurn(page: Page) {
  return page.locator('button[aria-expanded="false"]').filter({ hasText: /\d+ tools/ });
}

/** Click a piece, then its destination. Click-to-move, not drag — the same path a person uses. */
async function move(page: Page, from: string, to: string): Promise<void> {
  await page.locator(`[data-square="${from}"]`).click();
  await page.locator(`[data-square="${to}"]`).click();
}

test("a person sits down, moves, and the model answers", async ({ page }) => {
  const url = await sitDown(page);
  const board = page.locator("[data-fen]").first();

  await expect(board).toHaveAttribute("data-fen", START);

  await move(page, "e2", "e4");

  // White's pawn is on e4 — the server validated and disposed (invariant 1); the browser only
  // proposed. Whose move it is is deliberately not asserted: the model can reply before the next
  // poll, and a test that races the opponent is a flake.
  await expect(board).toHaveAttribute("data-fen", /4P3/);

  // The worker plays Black. When its move lands it is White's again, on move two.
  await expect(board).toHaveAttribute("data-fen", MODEL_HAS_REPLIED, { timeout: 45_000 });

  expect(url).toContain("/games/");
});

test("a game reloaded mid-play restores the exact position, history and costs", async ({
  page,
}) => {
  const url = await sitDown(page);
  const board = page.locator("[data-fen]").first();

  await move(page, "d2", "d4");
  await expect(board).toHaveAttribute("data-fen", MODEL_HAS_REPLIED, { timeout: 45_000 });

  const before = await board.getAttribute("data-fen");
  const moves = await page.getByRole("button", { name: /\d+ tools/ }).count();

  await page.goto(url);

  // The exact position, not merely a position. Reconnect reads the same event log the live
  // stream appends to (ADR-0008), so a mismatch here means the two have drifted.
  await expect(page.locator("[data-fen]").first()).toHaveAttribute("data-fen", before!);
  await expect(page.getByRole("button", { name: /\d+ tools/ })).toHaveCount(moves);

  // The cost rail survives the reload too — it is read back, not accumulated in the browser.
  await expect(page.getByText(/cost/i).first()).toBeVisible();
});

test("the model's thinking is hidden while the game is live and readable once it is over", async ({
  page,
}) => {
  /**
   * Invariant 8 and HUMAN-07: *never exposed mid-game*. A person is sitting at the table, so
   * streaming their opponent's plan to them would hand them the game — and unlike a spectator of
   * a model-vs-model game, they are a participant.
   *
   * The gate runs on the way out (`api/redaction.py`), not when the event is written, so the text
   * is in the log throughout and appears the moment the game ends. It used to be dropped at write
   * time, which — the log being append-only (ADR-0008) — made a person's own games the only ones
   * whose reasoning the transcript could never show.
   *
   * The scripted opponent reasons precisely so this cannot pass vacuously: "no reasoning is
   * visible" holds trivially against a model that never produced any.
   */
  const url = await sitDown(page);
  const board = page.locator("[data-fen]").first();

  await move(page, "e2", "e4");
  await expect(board).toHaveAttribute("data-fen", MODEL_HAS_REPLIED, { timeout: 45_000 });

  // The turn is there, and expanding it shows the moves — but not the thinking behind them.
  await foldedTurn(page).first().click();
  await expect(page.getByText(/make_move/).first()).toBeVisible();
  await expect(page.getByText(SCRIPTED_REASONING)).toHaveCount(0);

  await page.getByRole("button", { name: "resign", exact: true }).click();
  await page.getByRole("button", { name: "confirm resign" }).click();

  // Wait for the result before reloading. Navigating on the click raced the request, and the
  // reloaded page was still the live view.
  await expect(page.getByText(/0-1|resignation/i).first()).toBeVisible();

  // Over — and now the same turn gives its reasoning up.
  await page.goto(url);
  const stillFolded = foldedTurn(page);
  if (await stillFolded.count()) await stillFolded.first().click();

  await expect(page.getByText(SCRIPTED_REASONING).first()).toBeVisible();
});

test("resigning ends the game, and the link becomes the replay", async ({ page }) => {
  const url = await sitDown(page);

  // Two steps: resigning is irreversible and a stray click should not end a game.
  await page.getByRole("button", { name: "resign", exact: true }).click();
  await page.getByRole("button", { name: "confirm resign" }).click();

  await expect(page.getByText(/0-1|resignation/i).first()).toBeVisible();

  // The share link handed out mid-game keeps working; it simply becomes the replay (Phase 8).
  await page.goto(url);
  await expect(page.getByRole("slider", { name: "Ply" })).toBeVisible();
});

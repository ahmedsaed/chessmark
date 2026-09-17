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
 * The newest turn in the conversation, open, with its steps on screen.
 *
 * **The newest turn is usually open already, and waiting for a folded one hangs.** `EventStream`
 * unfolds the focused turn without being asked, and in a live human game the model's single turn
 * *is* the focus — so `button[aria-expanded="false"]` never matches and the click waits out the
 * timeout. This suite is the one project CI does not run, so it stayed red from the day that
 * default landed. The public replay suite hit the same edge and guards it the same way.
 *
 * Scoped to the turn rather than to `aria-expanded` across the page: the account button in the
 * site header is a collapsed disclosure too, and it sorts first in the document.
 *
 * The steps are awaited rather than the click, because the callers below go on to assert that
 * something is **absent** — and against a turn that never opened, that assertion cannot fail.
 */
async function openNewestTurn(page: Page) {
  const turn = page.getByTestId("turn").last();
  const fold = turn.locator("button[aria-expanded]").filter({ hasText: /\d+ steps?/ }).first();
  await expect(fold).toBeVisible();
  if ((await fold.getAttribute("aria-expanded")) === "false") await fold.click();

  const steps = turn.getByTestId("turn-steps");
  await expect(steps).toBeVisible();
  return steps;
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

  // The turn is open and its steps are on screen — the move is right there — and the thinking
  // behind it is not merely collapsed but absent: no reasoning step was sent to this browser at
  // all. Asserting the text is missing would also hold if the turn had a folded reasoning block
  // in it, which is a different and much weaker claim than invariant 8 makes.
  const live = await openNewestTurn(page);
  await expect(live.getByText(/make_move/).first()).toBeVisible();
  await expect(live.locator('[data-step="reasoning"]')).toHaveCount(0);
  await expect(page.getByText(SCRIPTED_REASONING)).toHaveCount(0);

  await page.getByRole("button", { name: "resign", exact: true }).click();
  await page.getByRole("button", { name: "confirm resign" }).click();

  // Wait for the result before reloading. Navigating on the click raced the request, and the
  // reloaded page was still the live view.
  await expect(page.getByText(/0-1|resignation/i).first()).toBeVisible();

  // Over — and now the same turn gives its reasoning up. The block is there to be opened, which
  // is the half of invariant 8 that a redaction bug at write time would have destroyed for ever:
  // the log is append-only, so text withheld on the way in is never recoverable.
  await page.goto(url);
  const over = await openNewestTurn(page);

  // Reasoning keeps its own fold, shut by default even on a finished game — it is the longest
  // thing in a turn and opening the turn into a wall of it buries the move.
  const reasoning = over.locator('[data-step="reasoning"]');
  await expect(reasoning).toHaveCount(1);
  const unroll = reasoning.locator('button[aria-expanded="false"]').first();
  if (await unroll.count()) await unroll.click();

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

test("a game you finished is counted on your profile, and reachable from it", async ({ page }) => {
  /**
   * The populated half of the profile (HUMAN-03). `account.spec.ts` asserts the empty state on an
   * account created seconds earlier; this one creates the game it then counts, so neither depends
   * on what a previous run happened to leave in the database.
   *
   * **The arithmetic is the assertion, not the numbers.** Played is every game; W/D/L counts only
   * the decided ones, and a harness stop is counted apart from both (invariant 11). A panel whose
   * columns do not add up to its own total is the failure worth catching, and it is one a fixed
   * expected number would not see.
   */
  const url = await sitDown(page);

  await page.getByRole("button", { name: "resign", exact: true }).click();
  await page.getByRole("button", { name: "confirm resign" }).click();
  await expect(page.getByText(/0-1|resignation/i).first()).toBeVisible();

  await page.goto("/profile");

  const cell = (label: string) =>
    page.locator("dl div", { has: page.getByText(label, { exact: true }) }).locator("dd");

  await expect(cell("Played")).not.toHaveText("0");

  const played = Number(await cell("Played").innerText());
  const [wins, draws, losses] = (await cell("W / D / L").innerText())
    .split("/")
    .map((part) => Number(part.trim()));
  const running = Number(await cell("In progress").innerText());
  const undecided = Number(await cell("No result").innerText());

  expect(wins + draws + losses + running + undecided).toBe(played);
  // The game just resigned is a loss, so the decided column cannot be empty.
  expect(wins + draws + losses).toBeGreaterThan(0);

  // And the history is a way back to the board, not a tally. The card links to the game.
  await expect(page.getByRole("link", { name: /Chessmark/ }).first()).toBeVisible();
  await page.goto(url);
  await expect(page.getByRole("slider", { name: "Ply" })).toBeVisible();
});

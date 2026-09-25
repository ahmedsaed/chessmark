import { expect, test } from "@playwright/test";

import { fixtures } from "../fixtures";

/**
 * The archive, `/games` (UI-12).
 *
 * The API's filters are pinned by `tests/api/test_archive.py`; what only a browser can check is
 * that the page's state really is its address — that a filter lands in the URL, that the URL
 * alone reproduces the list, and that the form still works as a form.
 */

const rows = "ol > li";

test("the archive lists the seeded game and opens it", async ({ page }) => {
  const { replayGame } = fixtures();
  await page.goto("/games");

  const link = page.locator(`${rows} a[href="/games/${replayGame}"]`);
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/games/${replayGame}$`));
});

test("a filter is written to the address, and the address alone reproduces it", async ({
  page,
}) => {
  const { replayGame, result } = fixtures();
  /* The side that did *not* win the seeded game, so the filter must drop it — a filter asserted
     only on a list it keeps whole passes when it does nothing. */
  const other = result === "1-0" ? "black" : "white";

  await page.goto("/games");
  await expect(page.locator(`a[href="/games/${replayGame}"]`)).toBeVisible();

  await page.getByLabel("Result").selectOption(other);
  await expect(page).toHaveURL(new RegExp(`/games\\?result=${other}$`));
  await expect(page.locator(`a[href="/games/${replayGame}"]`)).toHaveCount(0);

  // Reloaded from nothing but the URL: the list and the control both come back as they were.
  await page.reload();
  await expect(page.getByLabel("Result")).toHaveValue(other);
  await expect(page.locator(`a[href="/games/${replayGame}"]`)).toHaveCount(0);

  await page.getByRole("link", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/games$/);
  await expect(page.getByLabel("Result")).toHaveValue("");
});

test("a search that matches nothing says so, and offers the way back", async ({ page }) => {
  await page.goto("/games");
  await page.getByLabel("Search by model or player").fill("zz-no-such-player-zz");
  await page.getByRole("button", { name: "Search" }).click();

  await expect(page).toHaveURL(/\/games\?q=zz-no-such-player-zz$/);
  await expect(page.getByText("No games match these filters.")).toBeVisible();
  await expect(page.locator(rows)).toHaveCount(0);
});

test("the form without JavaScript lands on the same address", async ({ request }) => {
  /* What a plain GET form submits: every field, empty ones and spelled-out defaults included. */
  const response = await request.get(
    "/games?q=&show=played&result=&ending=&players=&ranked=&sort=newest&model=&vs=&event=",
    { maxRedirects: 0 },
  );
  expect(response.status()).toBe(307);
  expect(response.headers().location).toMatch(/\/games$/);
});

test("aborted games are hidden until asked for", async ({ page }) => {
  await page.goto("/games");
  await expect(page.locator(rows).first()).toBeVisible();
  await expect(page.locator(rows).filter({ hasText: /^aborted$/im })).toHaveCount(0);

  await page.goto("/games?show=aborted");
  const listed = await page.locator(rows).count();
  test.skip(listed === 0, "no aborted games in this database — nothing to reveal");
  await expect(page.locator(rows).filter({ hasText: /aborted/i })).toHaveCount(listed);
});

test("load more appends the next page below, without leaving the page", async ({ page }) => {
  await page.goto("/games?show=all");
  const button = page.getByRole("link", { name: "Load more" });
  /* Skipped loudly, never silently green: a page is fifty games and the suite may seed fewer. */
  test.skip((await button.count()) === 0, "fewer than one page of games — nothing to load");

  const hrefs = () =>
    page.locator("ol > li a").evaluateAll((a) => a.map((x) => x.getAttribute("href")));
  const first = await hrefs();
  const url = page.url();

  // Scrolled to the button first: the complaint that replaced older/newer was being thrown back
  // to the top of the list. Where the reader was is where they must still be.
  await button.scrollIntoViewIfNeeded();
  const scrolled = await page.evaluate(() => window.scrollY);
  await button.click();

  await expect(page.locator("ol > li")).not.toHaveCount(first.length);
  const after = await hrefs();
  expect(after.slice(0, first.length), "the first page stays where it was").toEqual(first);
  expect(new Set(after).size, "no game is listed twice").toBe(after.length);
  expect(page.url(), "the address does not change").toBe(url);
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(scrolled - 1);
});

test("load more without JavaScript is a link to the next page", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto("/games?show=all");
  const button = page.getByRole("link", { name: "Load more" });
  test.skip((await button.count()) === 0, "fewer than one page of games — nothing to load");

  const lastOnFirst = await page.locator("ol > li a").last().getAttribute("href");
  await button.click();
  await expect(page).toHaveURL(/before=/);
  expect(await page.locator("ol > li a").first().getAttribute("href")).not.toBe(lastOnFirst);
  await context.close();
});

test("the header still fits at the narrowest width that shows its links", async ({ page }) => {
  /* 768px is `md`, where the bar switches from the menu glyph to six links. Adding Games pushed
     Sign up 62px past the right edge there; nothing narrower or wider showed it. */
  await page.setViewportSize({ width: 768, height: 800 });
  await page.goto("/games");

  const edge = await page.evaluate(() => document.documentElement.clientWidth);
  const controls = page.locator("header a:visible, header button:visible");
  const rights = await controls.evaluateAll((els) =>
    els.map((el) => el.getBoundingClientRect().right),
  );
  expect(Math.max(...rights), "every header control should be on screen").toBeLessThanOrEqual(edge);
});

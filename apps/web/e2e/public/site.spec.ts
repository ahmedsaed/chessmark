import { expect, test } from "@playwright/test";

/**
 * The shell and the public reading surface (Phases 7, 18, 20 — AUTH-02).
 *
 * Reading is open to everyone, so all of this must render with no identity whatsoever. Before
 * Phase 18 the root layout rendered `{children}` and nothing else and every page hand-rolled its
 * own back-link; nothing but a person's eyes would have noticed it regress.
 */

test("every public page renders with the shell and no console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  for (const path of ["/", "/about", "/leaderboard", "/models", "/play"]) {
    const response = await page.goto(path);
    expect(response?.status(), `${path} should not be an error page`).toBeLessThan(400);

    // The header and footer come from the root layout. A page that renders without them is the
    // exact regression Phase 18 fixed.
    await expect(page.locator("header").first()).toBeVisible();
    await expect(page.locator("footer").first()).toBeVisible();
  }

  expect(errors, "the console should be clean").toEqual([]);
});

test("the landing page leads with a game", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // A board is the point of the page. It is rendered as a grid of squares by react-chessboard.
  await expect(page.locator("[data-column]").first()).toBeVisible();
});

test("the models page filters in the browser, without a request per keystroke", async ({
  page,
}) => {
  await page.goto("/models");

  const search = page.getByLabel("Search models");
  await expect(search).toBeVisible();

  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));

  // Typed a character at a time, because "one request per keystroke" is the failure mode this
  // guards against and `fill()` would produce a single input event.
  await search.pressSequentially("anthropic", { delay: 30 });
  await expect(page.getByRole("heading", { name: /anthropic/i }).first()).toBeVisible();

  // The whole catalogue is fetched once by the page above; filtering is local. This asserts the
  // choice rather than merely permitting it.
  //
  // Scoped to the **API origin**, not to any URL containing "/models". A production build
  // prefetches its own routes over RSC, so `localhost:3010/models?_rsc=…` shows up here in CI and
  // not under `next dev` — a router concern that says nothing about whether filtering hits the
  // network. What must stay at zero is calls to the catalogue endpoint.
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  expect(requests.filter((url) => url.startsWith(api))).toEqual([]);
});

test("a model page reaches the games behind its numbers", async ({ page }) => {
  await page.goto("/models");
  await page.getByLabel("Search models").fill("gemini");

  const first = page.locator('a[href^="/models/"]').first();
  await expect(first).toBeVisible();
  await first.click();

  await expect(page).toHaveURL(/\/models\/.+\/.+/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // Registry facts — the three that decide whether a model can finish a game (UI-07).
  for (const label of ["Input", "Output", "Context", "Reasoning"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
});

test("an unknown model is a 404, not a crash", async ({ page }) => {
  const response = await page.goto("/models/nobody/nothing");

  expect(response?.status()).toBe(404);
});

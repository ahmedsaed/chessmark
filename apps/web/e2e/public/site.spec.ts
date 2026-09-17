import { expect, test } from "@playwright/test";

import { fixtures } from "../fixtures";

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

  for (const path of ["/", "/about", "/leaderboard", "/models", "/play", "/tournaments"]) {
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

test("the old contestant URL still lands on the model, with a real 308", async ({ page }) => {
  /**
   * `/leaderboard/{slug}?q=fp8` was a page of its own until ADR-0034 folded the contestant into
   * the model page as a block per precision.
   *
   * The redirect is in `next.config.ts`, not a `permanentRedirect()` from the route, because
   * `app/leaderboard/loading.tsx` covers that segment and everything under it — and a streamed
   * response commits its status line before the page body runs. From the page it would have
   * answered `200` and moved the browser client-side: invisible in a browser, wrong to every
   * crawler and link checker. Exactly the trap the 404 assertions below exist for, in the other
   * direction, which is why the status is asserted rather than the destination alone.
   */
  const response = await page.request.get("/leaderboard/vendor%2Fmodel?q=fp8", {
    maxRedirects: 0,
  });

  expect(response.status()).toBe(308);
  const location = response.headers()["location"];
  expect(location).toContain("/models/vendor%2Fmodel");
  expect(location).toContain("#c-fp8");
});

test("an unknown model is a 404, not a crash", async ({ page }) => {
  const response = await page.goto("/models/nobody/nothing");

  expect(response?.status()).toBe(404);
});


// ====================================================================== tournaments

test("the tournaments index lists what has been run", async ({ page }) => {
  await page.goto("/tournaments");

  await expect(page.getByRole("heading", { level: 1, name: /tournaments/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /E2E Cup/ })).toBeVisible();
});

test("a tournament page shows its table, its schedule and what it cost", async ({ page }) => {
  const slug = fixtures().tournament;
  test.skip(!slug, "the catalogue was too small to field a tournament");

  await page.goto(`/tournaments/${slug}`);

  await expect(page.getByRole("heading", { level: 1, name: "E2E Cup" })).toBeVisible();

  // The table. Four entrants, and the seeded results mean somebody is ahead of somebody.
  const standings = page.getByRole("link", { name: /^[a-z0-9.\-]+/ });
  await expect(page.getByText("Standings")).toBeVisible();
  await expect(standings.first()).toBeVisible();

  // The schedule, with more than one state represented — the seed settles two pairings and
  // abandons a third precisely so this cannot pass against a page that renders only one.
  await expect(page.getByText("Schedule")).toBeVisible();
  await expect(page.getByText(/Round 1/)).toBeVisible();
  await expect(page.getByTitle("played").first()).toBeVisible();
  await expect(page.getByTitle("abandoned").first()).toBeVisible();

  // Every figure on the page traces to the call log (invariant 4); these are the labels it
  // prints them under.
  for (const label of ["Cost", "Tokens", "Plies", "Decisive", "Illegal"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
});

test("an unknown tournament is a 404, not a crash", async ({ page }) => {
  const response = await page.goto("/tournaments/never-happened");

  expect(response?.status()).toBe(404);
});


// ====================================================================== streaming vs. status

/**
 * A missing page must answer `404`, and a `loading.tsx` above it silently prevents that.
 *
 * The boundary makes its whole segment stream, and a streamed response commits its status line
 * before the page body runs — so `notFound()` renders the right page under a `200`. Search engines
 * and link checkers read the status, not the page.
 *
 * It is an easy regression to reintroduce, because the fix for a slow-feeling click is exactly a
 * `loading.tsx` and the damage is invisible in a browser. The three routes that can 404 are
 * asserted together so that adding one boundary "for consistency" fails here rather than in
 * production.
 */
for (const [what, path] of [
  ["a game", "/games/00000000-0000-0000-0000-000000000000"],
  ["a model", "/models/nobody/nothing"],
  ["a tournament", "/tournaments/never-happened"],
] as const) {
  test(`${what} that does not exist answers 404, not a streamed 200`, async ({ page }) => {
    const response = await page.goto(path);

    expect(response?.status()).toBe(404);
  });
}

// ====================================================================== social cards

/**
 * Every public page carries a social card (UI-06).
 *
 * **This is the assertion that was missing, and the bug it would have caught was total.** Metadata
 * keys are inherited wholesale, so `pageMetadata` setting an `openGraph` block replaced the root's
 * — including the `images` the file convention injects — and seven routes shipped with no
 * `og:image` at all. Sharing `/leaderboard` anywhere produced a bare link. Nothing in a browser
 * looks wrong, nothing in the build warns, and the only place it shows is somebody else's timeline.
 *
 * Asserted for every route at once, so a page added without a card fails here rather than being
 * discovered in a screenshot months later.
 */
test("every public page has a social card", async ({ page }) => {
  const { replayGame, tournament } = fixtures();
  const paths = [
    "/",
    "/leaderboard",
    "/models",
    "/play",
    "/tournaments",
    "/about",
    "/methodology",
    `/games/${replayGame}`,
    ...(tournament ? [`/tournaments/${tournament}`] : []),
  ];

  for (const path of paths) {
    await page.goto(path);

    const image = page.locator('meta[property="og:image"]');
    // Exactly one: a page that names the root's card *and* colocates its own would emit two and
    // leave the choice to whichever unfurler read it.
    await expect(image, `${path} should have one og:image`).toHaveCount(1);

    const url = await image.getAttribute("content");
    expect(url, `${path}'s og:image should be absolute`).toMatch(/^https?:\/\//);
  }
});

/**
 * And the card is a real image, not a 404 or an error page with an image content type.
 *
 * A generated card reads live data, so it can fail in ways the page it belongs to does not — an
 * API timeout inside `ImageResponse` is a broken-image box next to a link that works. Fetched
 * rather than rendered: what an unfurler does is exactly this request.
 */
test("every social card renders", async ({ page, request }) => {
  const { replayGame, tournament } = fixtures();
  const paths = [
    "/",
    "/leaderboard",
    "/models",
    "/tournaments",
    `/games/${replayGame}`,
    ...(tournament ? [`/tournaments/${tournament}`] : []),
  ];

  for (const path of paths) {
    await page.goto(path);
    const url = await page.locator('meta[property="og:image"]').getAttribute("content");

    /* **Not followed.** A redirect that lands on *a* valid card passes a naive check and is still
       wrong: `/leaderboard/:slug → /models/:slug` runs before routing and served the models card
       for the leaderboard, 200 and several hundred KB of perfectly good PNG. The card a page names
       has to be the card that page's URL returns. */
    const response = await request.get(url!, { maxRedirects: 0 });

    expect(response.status(), `${path}'s card should render, not redirect`).toBe(200);
    expect(response.headers()["content-type"]).toContain("image/png");
    // A blank 1200×630 PNG is a couple of hundred bytes; anything real is far larger. This is the
    // cheapest way to tell "it drew something" from "it drew nothing and returned cleanly".
    expect((await response.body()).byteLength).toBeGreaterThan(5_000);
  }
});

/**
 * A card for a record that does not exist is still a card.
 *
 * `notFound()` is right for the page and wrong for its image: an unfurler that gets a 404 for the
 * image draws a broken-image box, which reads as "this site is broken" rather than "that page is
 * gone". Each card falls back to a plain titled image instead.
 */
test("a missing record still renders a card rather than a broken image", async ({ request }) => {
  for (const path of [
    "/og/model/nobody/nothing",
    "/tournaments/never-happened/opengraph-image",
  ]) {
    const response = await request.get(path);

    expect(response.status(), `${path} should render`).toBe(200);
    expect(response.headers()["content-type"]).toContain("image/png");
  }
});

// ====================================================================== the Clerk UI bundle

/**
 * **`@clerk/ui` must never be fetched** (NFR-12).
 *
 * One prebuilt Clerk component anywhere — a `<SignIn />`, a `<UserButton />`, a
 * `SignInButton mode="modal"` — puts **285 KiB** on *every* route, `/about` and `/leaderboard`
 * included. That is what `prefetchUI={false}` turns off and what owning `AuthForm` and
 * `ProfileView` paid for, and it is re-broken silently by adding one import.
 *
 * Asserted on the network rather than by grepping the source, because the failure is a *request*:
 * a component could arrive through a dependency and no import of ours would show it.
 *
 * Skipped loudly without Clerk — with no publishable key `AuthProvider` renders nothing, so the
 * bundle is trivially absent and the test would pass while proving nothing. Same bargain the
 * signed-in project strikes.
 */
test("the Clerk UI bundle is never requested", async ({ page }) => {
  await page.goto("/sign-in");

  const configured = await page.evaluate(() =>
    Boolean(document.querySelector('script[src*="clerk"]')),
  );
  test.skip(!configured, "Clerk is not configured here — nothing to load, nothing to prove");

  const ui: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    // `@clerk/ui` ships as `ui.browser.js`, `ui-common_*`, `vendors_ui_*`, `framework_ui_*`.
    if (/clerk/.test(url) && /\bui[._-]|vendors_ui|framework_ui/.test(url)) ui.push(url);
  });

  for (const path of ["/", "/leaderboard", "/sign-in", "/sign-up", "/profile"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }

  expect(ui, "a prebuilt Clerk component is loading @clerk/ui site-wide").toEqual([]);
});

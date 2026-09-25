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

  for (const path of ["/", "/about", "/leaderboard", "/games", "/models", "/play", "/tournaments"]) {
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
    "/games",
    "/games?result=draw",
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
    "/games",
    "/games?result=draw",
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

/**
 * The podium (the lobby's leaderboard section).
 *
 * **Skipped loudly when nothing is ranked**, the same bargain the phone suite strikes on the
 * leaderboard itself: this suite's games are played by the scripted provider against
 * `vendor/model-N`, which `ratable.judge` excludes, so a run with no ranked fixture has no podium
 * to measure. It is the assertion that matters locally against `make dev-pull` data, where the
 * production slugs are 38 characters and the columns are 200px.
 */
test("the lobby stands the top three on a podium, tallest first", async ({ page }) => {
  await page.goto("/");

  const places = page.getByRole("list", { name: "The top three" }).locator("> li");
  const count = await places.count();
  /* Counted before anything measures, because `boundingBox()` waits for an element that is never
     coming and the suite would hang rather than skip. */
  test.skip(count < 3, "fewer than three ranked contestants in this database");

  const boxes = await Promise.all(
    [0, 1, 2].map(async (index) => {
      const box = await places.nth(index).boundingBox();
      expect(box, `place ${index + 1} should have a box`).not.toBeNull();
      return box!;
    }),
  );

  /* **This is the podium, and it is the only thing that says so.** The cards carry a rank nowhere
     a desktop reader can see it — the `#1` badge is `sm:hidden` and the plinth numeral is
     `aria-hidden` decoration — so the ranking is communicated by height alone. A card that stopped
     standing taller than the one below it would still render, still link correctly, and still say
     nothing, which is precisely the failure mode this file exists for. */
  expect(boxes[0].y, "first place should stand highest").toBeLessThan(boxes[1].y);
  expect(boxes[1].y, "second place should stand above third").toBeLessThan(boxes[2].y);

  // And they stand on the same floor: a plinth is a *height*, not an offset.
  const floor = boxes.map((box) => Math.round(box.y + box.height));
  expect(new Set(floor).size, `the three places should share a base, got ${floor}`).toBe(1);

  // 2 · 1 · 3 — first place is in the middle, which is what makes it read as a podium rather than
  // a staircase. Only at desktop width; the phone suite asserts the stacked layout.
  expect(boxes[1].x, "second place is painted to the left of first").toBeLessThan(boxes[0].x);
  expect(boxes[0].x, "third place is painted to the right of first").toBeLessThan(boxes[2].x);
});

test("the lobby's ranking runs 1 to 10 across the podium and the list beside it", async ({
  page,
}) => {
  /* **Checked against the API, not against itself.** The podium and the list are two slices of one
     array, and a slice's mistakes are silent: skip a contestant and the ten shown are still in
     descending order, show one twice and they still are. Only the ranking this page was handed can
     tell, so this test asks for it. */
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  const board = await page.request.get(`${api}/leaderboard`);
  expect(board.ok(), "the leaderboard endpoint should answer").toBe(true);

  const ranked = (await board.json()).rows as { rating: number }[];
  test.skip(ranked.length === 0, "no ranked contestants in this database");

  await page.goto("/");

  const figures = await page
    .getByTestId("rating")
    .evaluateAll((spans) => spans.map((span) => Number.parseInt(span.textContent ?? "", 10)));

  expect(figures, "the lobby shows the API's top ten, in its order, once each").toEqual(
    ranked.slice(0, 10).map((row) => Math.round(row.rating)),
  );

  // Three of those stand on the podium, so the list beside it holds the rest — not six, not eight.
  const listed = await page
    .getByRole("list", { name: "Places four onward" })
    .locator("> li")
    .count();
  expect(listed).toBe(Math.max(0, figures.length - 3));
});

/**
 * No page may ask for the same URL over and over.
 *
 * A `<Link>` on screen prefetches, and a payload the router cannot store — every route here is
 * dynamic, so every one of them is `no-store` — leaves the prefetch task dirty and schedules it
 * again. `/leaderboard` and `/tournaments/{slug}` were doing this on production to every reader
 * with the tab open: ~90 requests in twelve seconds per link there, ~110 a second on a production
 * build locally. Nothing reached the console, no page looked wrong, and `make check` had nothing
 * to say about it — the only symptom was load, on a server nobody was watching that closely.
 *
 * So the assertion is the *property*, not the workaround: a page settles. Whatever a future change
 * does — a new link to a model, a different fix, a framework upgrade that makes `prefetch={false}`
 * unnecessary — this still says whether the site sits quietly once it has loaded.
 */
test("a loaded page stops asking for things", async ({ page }) => {
  const counts = new Map<string, number>();
  page.on("request", (request) => {
    const url = request.url().replace(/\?.*/, "");
    counts.set(url, (counts.get(url) ?? 0) + 1);
  });

  const tournament = fixtures().tournament;
  for (const path of ["/", "/leaderboard", ...(tournament ? [`/tournaments/${tournament}`] : [])]) {
    counts.clear();
    await page.goto(path);
    /* Long enough for the loop to be unmistakable — it ran at a hundred requests a second — and
       short enough not to lengthen the suite. A settled page makes no requests at all in this
       window; the ceiling below is slack for a retry or a router prefetch, not a budget. */
    await page.waitForTimeout(4000);

    const worst = [...counts.entries()].sort((a, b) => b[1] - a[1])[0] ?? ["nothing", 0];
    expect(worst[1], `${path} kept re-requesting ${worst[0]}`).toBeLessThan(12);
  }
});

/**
 * The lobby's tournaments section.
 *
 * Two things it is easy to ship broken, both of which happened while it was being written:
 *
 * * **A row built for more cells than exist.** A fixed four-wide grid put this deployment's two
 *   cells — the explainer and the one running event — in the left half and left the right half
 *   empty, which reads as a section waiting for something rather than a section.
 * * **Numbers that are not the API's.** The card states a tournament's progress; the only way to
 *   know it states *this* tournament's progress is to ask the same endpoint it was rendered from.
 */
test("the lobby explains what a tournament is, and fills its row with the ones there are", async ({
  page,
}) => {
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  const response = await page.request.get(`${api}/tournaments?limit=20`);
  expect(response.ok(), "the tournaments endpoint should answer").toBe(true);

  const tournaments = (await response.json()) as { name: string; stats: Record<string, number> }[];
  test.skip(tournaments.length === 0, "no tournaments in this database");

  await page.goto("/");

  // The concept, which is the reason the section is not just a second copy of `/tournaments`.
  await expect(page.getByRole("heading", { name: "What a tournament is" })).toBeVisible();

  /* The cells reach the end of the row. Measured against the section's own width rather than a
     column count, because what went wrong was empty space, and that is what empty space looks
     like from outside. */
  const row = await page.evaluate(() => {
    const heading = [...document.querySelectorAll("h2")].find(
      (h) => h.textContent?.trim() === "Tournaments",
    )!;
    const grid = heading.closest("section")!.querySelector<HTMLElement>(".grid")!;
    const cells = [...grid.children].map((cell) => cell.getBoundingClientRect());
    const box = grid.getBoundingClientRect();
    return {
      cells: cells.length,
      /* How far the last cell stops short of the grid's right edge. A half-empty row is half the
         grid wide; a full one is a couple of pixels of rounding. */
      shortBy: Math.round(box.right - Math.max(...cells.map((c) => c.right))),
      width: Math.round(box.width),
    };
  });

  expect(row.cells, "one cell per tournament shown, plus the explainer").toBe(
    Math.min(tournaments.length, 3) + 1,
  );
  expect(
    row.shortBy,
    `the row is ${row.shortBy}px short of its own width (${row.width}px)`,
  ).toBeLessThan(4);
});

test("a tournament card states the tournament's own numbers", async ({ page }) => {
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  const response = await page.request.get(`${api}/tournaments?limit=20`);
  const tournaments = (await response.json()) as {
    name: string;
    status: string;
    entrant_count: number;
    stats: { played: number; pairings: number };
  }[];
  test.skip(tournaments.length === 0, "no tournaments in this database");

  /* The lobby shows running events first, so the card to check is the one the page would pick. */
  const [first] = [...tournaments].sort(
    (a, b) => Number(b.status === "running") - Number(a.status === "running"),
  );

  await page.goto("/");

  const card = page.locator('a[href^="/tournaments/"]').first();
  await expect(card).toContainText(first.name);
  await expect(card).toContainText(`${first.entrant_count} entrants`);
  // The progress line, which is the one thing here that is not on `/tournaments` already.
  await expect(card).toContainText(`${first.stats.played} of ${first.stats.pairings} pairing`);
});

/**
 * The lobby's invitation to play.
 *
 * Both halves state numbers, and both are the kind of number that is easy to render from the wrong
 * place: the scoreboard is the human record from `/games/human-record`, and "know your opponent" is
 * summed from the ranking the page already holds. A section whose whole point is that the figures
 * are real has to be checked against the source, not against itself.
 */
test("the lobby's scoreboard and charge sheet are the API's numbers", async ({ page }) => {
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  const [recordResponse, boardResponse] = await Promise.all([
    page.request.get(`${api}/games/human-record`),
    page.request.get(`${api}/leaderboard`),
  ]);
  expect(recordResponse.ok(), "the human record should answer").toBe(true);

  const record = (await recordResponse.json()) as { wins: number; losses: number; games: number };
  const board = (await boardResponse.json()) as {
    games_counted: number;
    rows: { illegal_attempts: number }[];
  };

  await page.goto("/");

  const section = page.locator("section", { has: page.getByRole("link", { name: "Take a seat →" }) });
  await expect(section).toBeVisible();

  /* The score, both sides, in the order they are painted: humans first. A scoreboard that reads
     the record backwards is the one mistake here nobody would notice from a screenshot. */
  const score = await section.locator("p", { hasText: /^(Humans|Models)/ }).allInnerTexts();
  expect(score.map((line) => line.split("\n").pop()?.trim())).toEqual([
    String(record.wins),
    String(record.losses),
  ]);

  const illegal = board.rows.reduce((total, row) => total + row.illegal_attempts, 0);
  await expect(section).toContainText(`${illegal.toLocaleString("en")}`);
  await expect(section).toContainText(
    `illegal moves attempted in ${board.games_counted} ranked game`,
  );
});

/**
 * The turn excerpt on the lobby.
 *
 * It is the one section built by folding a real event log rather than reading fields off a
 * summary, so the failure mode is not an empty box — it is a *plausible* box: a quotation from the
 * wrong game, or a fragment of a prompt template dressed up as a thought. Both render beautifully.
 *
 * `</role>` is not hypothetical. The first build of this picked a turn whose entire published
 * reasoning was that, two tokens of it, under the heading "inside one turn".
 */
test("the turn on the lobby is a real turn of the game it links to", async ({ page }) => {
  await page.goto("/");

  const heading = page.getByRole("heading", { name: "Inside one turn" });
  test.skip(
    (await heading.count()) === 0,
    "no finished game in this database published enough reasoning to show",
  );

  const section = page.locator("section", { has: heading });
  const href = await section.getByRole("link", { name: "Open the game →" }).getAttribute("href");
  expect(href, "the excerpt should link to its game").toMatch(/^\/games\//);

  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
  const game = (await (await page.request.get(`${api}${href!.replace("/games/", "/games/")}`)).json()) as {
    moves: string[];
    players: { display_name: string; colour: string }[];
  };

  /* The header names a seat of *that* game. On its own this is weak — two games share a model —
     so the move below is what actually ties the excerpt to the game it links to. Together they
     catch the mistake nobody could see: an excerpt and a link from different games. */
  const text = await section.innerText();
  expect(
    game.players.some((player) => text.includes(player.display_name)),
    `the excerpt names neither seat of the game it links to`,
  ).toBe(true);

  // The move it says was played is a move that game actually contains.
  const played = await section.getByText(/^played /).innerText();
  expect(game.moves).toContain(played.replace("played", "").trim());

  /* And it is a thought, not an artefact. Measured on the rendered text rather than trusted from
     the picker, because the clamp and the fold both sit between them. */
  const thought = await section.locator("p").first().innerText();
  expect(thought.trim().length, `the excerpt shows "${thought}"`).toBeGreaterThan(80);
});

/**
 * The closing strip is three answers and three doors. The doors are the part that rots.
 *
 * Its whole design is that it does not repeat what `/about` and `/methodology` say — which makes
 * it entirely dependent on those routes still existing under those names. A renamed page turns
 * the honest short answer into a 404, and nothing else on the site would notice.
 */
test("every answer on the lobby leads somewhere", async ({ page }) => {
  await page.goto("/");

  const strip = page.locator("section", {
    has: page.getByRole("heading", { name: "Before you ask" }),
  });
  await expect(strip).toBeVisible();

  const links = await strip.getByRole("link").all();
  expect(links.length, "three questions, three doors").toBe(3);

  for (const link of links) {
    const href = await link.getAttribute("href");
    const response = await page.request.get(href!);
    expect(response.status(), `${href} should not be an error page`).toBeLessThan(400);
  }
});

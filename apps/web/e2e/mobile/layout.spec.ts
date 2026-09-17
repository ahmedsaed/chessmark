import { expect, test } from "@playwright/test";

import { fixtures } from "../fixtures";

/**
 * The site at phone width (UI-11).
 *
 * Every assertion here is a measurement that failed on production at 390px, and none of them is
 * something a unit test can see: `vitest.config.mts` keeps components out of vitest on the grounds
 * that layout is Playwright's, and layout is exactly what broke.
 *
 * The failures had a single shape — a flex row splitting a width that does not exist — and they
 * were invisible from a desk. Two `flex-1 truncate` names sharing a pairing row got **37px** each
 * against the 310px `nemotron-3-nano-omni-30b-a3b-reasoning:free` needs, so every fixture in the
 * pool read `nemo… vs nemo…`, and the full name lived in a `title` a thumb cannot open.
 */

test.describe("at phone width", () => {
  test("no page pushes itself sideways", async ({ page }) => {
    /* The cheapest regression test on the list and the one that caught a real mistake: collapsing
       the leaderboard's columns without also fixing its `table-auto` layout left an 463px table in
       a 333px page, which scrolled sideways rather than truncating. */
    for (const path of ["/", "/leaderboard", "/models", "/play", "/tournaments", "/about"]) {
      await page.goto(path);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, `${path} should not scroll horizontally`).toBeLessThanOrEqual(0);
    }
  });

  test("the leaderboard shows a rating without being scrolled", async ({ page }) => {
    await page.goto("/leaderboard");

    /* **Skipped loudly when the board is empty, never silently green.** The suite's games are
       played by the scripted provider against `vendor/model-N`, which `ratable.judge` excludes, so
       a run with no ranked fixture has no table to measure. Same bargain as the Clerk project. */
    const rating = page.getByRole("columnheader", { name: "Rating" });
    test.skip((await rating.count()) === 0, "no ranked games in this database — nothing to rank");

    /* The whole point of the page. It was an 820px table inside a 333px scroller, so a phone
       showed `#` and `Contestant` and the rating was off to the right with nothing saying so. */
    await expect(rating).toBeVisible();

    const box = await rating.boundingBox();
    const width = page.viewportSize()!.width;
    expect(box, "the Rating column should have a box").not.toBeNull();
    expect(
      box!.x + box!.width,
      "Rating should be on screen, not behind a swipe",
    ).toBeLessThanOrEqual(width);
  });

  test("the standings put the model name before the arithmetic", async ({ page }) => {
    const slug = fixtures().tournament;
    test.skip(slug === null, "the catalogue was too small to field a tournament");

    await page.goto(`/tournaments/${slug}`);

    /* The same rule as the leaderboard's, on the table that always has rows here. Five fixed
       columns left the `1fr` model column with 37px; dropping the trailing ones gives it 173px.
       Measured against the row rather than a constant, so it holds at any phone width. */
    const share = await page.evaluate(() => {
      const link = document.querySelector("li a[href^='/models/']");
      if (!link) return null;
      const row = link.closest("li")!;
      return link.getBoundingClientRect().width / row.getBoundingClientRect().width;
    });

    expect(share, "the standings should list entrants").not.toBeNull();
    expect(share!, "the name should get most of the row, not a tenth of it").toBeGreaterThan(0.4);
  });

  test("a player's name gets the whole row, so it has nothing to be cut off by", async ({
    page,
  }) => {
    await page.goto(`/games/${fixtures().replayGame}`);
    await expect(page.locator("[aria-label^='captured']").first()).toBeVisible();

    /* **A share of the row, not `scrollWidth > clientWidth`.** Asserting the truncation directly is
       the obvious test and it cannot fail here: the suite plays `anthropic/claude-fable-5`, which
       fits at 390px either way, so it passed against the broken layout too. What actually changed
       is that the name now *has* the row — it was content-width beside the huddle and is `flex-1`
       above it — and that holds whatever the name says. `Nex AGI: Nex-N2.5-Pro (free)` on
       production is the case this stands in for: 209px wanted, 157px given. */
    const share = await page.evaluate(() => {
      const cap = document.querySelector("[aria-label^='captured']")!;
      const bar = cap.parentElement!;
      const name = bar.querySelector("span")!;
      return name.getBoundingClientRect().width / bar.getBoundingClientRect().width;
    });

    expect(share, "the name should have the row, not a share of it").toBeGreaterThan(0.6);
  });

  test("the captures sit below the name, and both nameplates are the same height", async ({
    page,
  }) => {
    await page.goto(`/games/${fixtures().replayGame}`);

    const heights = await page.evaluate(() =>
      [...document.querySelectorAll("[aria-label^='captured']")].map((cap) => {
        const bar = cap.parentElement!;
        const name = bar.querySelector("span")!;
        return {
          barHeight: Math.round(bar.getBoundingClientRect().height),
          below: cap.getBoundingClientRect().top > name.getBoundingClientRect().bottom - 2,
        };
      }),
    );

    expect(heights.length).toBeGreaterThan(0);
    for (const bar of heights) expect(bar.below, "captures belong on their own line").toBe(true);
    /* They were 30px and 17px, because `flex-wrap` wrapped the huddle inside the row — two
       nameplates flanking one board at different heights, for a reason a reader cannot see. */
    expect(new Set(heights.map((bar) => bar.barHeight)).size, "both bars, one height").toBe(1);
  });

  test("the stats are a tab away, not a conversation away", async ({ page }) => {
    await page.goto(`/games/${fixtures().replayGame}`);

    const stats = page.getByRole("complementary", { name: "Game statistics" });
    const info = page.getByRole("tab", { name: "Info" });

    // Stacked, the order was board, conversation, stats — so the cheapest facts on the page were
    // the least reachable, behind a scroll as long as the game.
    await expect(info).toBeVisible();
    await expect(stats).toBeHidden();

    await info.click();
    await expect(stats).toBeVisible();
    await expect(page.getByRole("tab", { name: "Moves" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  test("the conversation is bounded, so the newest turn is near", async ({ page }) => {
    await page.goto(`/games/${fixtures().replayGame}`);

    /* Unbounded, the panel was as tall as the game was long and scrolling to the newest turn meant
       scrolling past every older one. It scrolls inside itself; it only needed a height. */
    const panel = page.locator("[aria-label='Game panels'] ~ div").first();
    const height = (await panel.boundingBox())!.height;
    expect(height).toBeLessThanOrEqual(page.viewportSize()!.height * 0.7);
  });

  test("a pairing's two models are both legible", async ({ page }) => {
    const slug = fixtures().tournament;
    test.skip(slug === null, "the catalogue was too small to field a tournament");

    await page.goto(`/tournaments/${slug}`);

    /* **Stacked is the property; a pixel width is not.** The two names shared one row and got 37px
       each, so the fix is that they no longer share it — which holds whatever the names are. This
       suite's entrants are `vendor/model-N` and would fit side by side, so asserting a width here
       would pass on the fixture and prove nothing about `nemotron-3-nano-omni-...`. */
    const rows = await page.evaluate(() =>
      /* The pairing body, whether or not it is wrapped in a link: a queued pairing has no game to
         link to yet, and this suite's fixtures are mostly queued. Selecting through the `<a>` found
         nothing and the test passed its way to an empty list. */
      [...document.querySelectorAll("li > div.grid, li > a > div.grid")].map((row) => {
        const names = [...row.children].filter(
          (c) => /[a-z]/i.test(c.textContent ?? "") && !/^vs$/.test(c.textContent!.trim()),
        );
        if (names.length < 2) return null;
        const [first, second] = names.map((n) => n.getBoundingClientRect());
        return { stacked: second.top >= first.bottom - 2, sameWidth: Math.abs(first.width - second.width) < 2 };
      }),
    );

    const pairings = rows.filter((r) => r !== null);
    expect(pairings.length, "the schedule should list pairings").toBeGreaterThan(0);
    for (const row of pairings) {
      expect(row!.stacked, "the two models belong on separate lines").toBe(true);
      expect(row!.sameWidth, "and each gets the same full-width line").toBe(true);
    }
  });

  test("the transport can be hit with a thumb", async ({ page }) => {
    await page.goto(`/games/${fixtures().replayGame}`);

    /* 28×28 is comfortable with a cursor and a guess with a finger, on the control a phone reader
       uses most. 44 is the figure every platform guideline lands on. */
    for (const name of ["Start", "Previous ply", "Next ply", "End"]) {
      const box = await page.getByRole("button", { name, exact: true }).boundingBox();
      expect(box, `${name} should be on the page`).not.toBeNull();
      expect(box!.height, `${name} is a touch target`).toBeGreaterThanOrEqual(44);
      expect(box!.width, `${name} is a touch target`).toBeGreaterThanOrEqual(44);
    }
  });

  test("nothing secondary is smaller than 11px", async ({ page }) => {
    await page.goto("/leaderboard");

    /* The site's labels run 8.5px to 10px, which is most of what a phone reader is given. The
       floor is one rule in `globals.css` rather than 139 arbitrary values in markup. */
    const tooSmall = await page.evaluate(() =>
      [...document.querySelectorAll("body *")]
        .filter((el) => el.children.length === 0 && /\S/.test(el.textContent ?? ""))
        .filter((el) => (el as HTMLElement).offsetParent !== null)
        .map((el) => ({
          text: el.textContent!.trim().slice(0, 30),
          size: parseFloat(getComputedStyle(el).fontSize),
        }))
        .filter((found) => found.size < 11),
    );

    expect(tooSmall).toEqual([]);
  });
});

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

test("at phone width the navigation trigger sits with the account controls, not adrift", async ({
  page,
}) => {
  /**
   * It floated in the middle of the bar, reading as a third nav item nobody had asked for.
   *
   * Two siblings each carried `ml-auto`, and two auto margins in one flex row *share* the free
   * space rather than one of them taking it — so the trigger came to rest 157px into a 390px
   * header. Invisible to types, lint and every other test: the button was present, labelled,
   * clickable and the right size. Only its position was wrong.
   *
   * Asserted as the structural property rather than a coordinate: the trigger belongs to the
   * right-hand group. A pixel assertion would break on any copy change, and would not say why.
   */
  await page.goto("/");

  const trigger = page.getByRole("button", { name: "Navigation" });
  await expect(trigger).toBeVisible();

  /* **Counted before it is measured.** `boundingBox()` auto-waits for its element, so on a build
     with no Clerk keys — which is what CI runs — asking for the box of a `sign in` link that will
     never exist does not return null, it hangs until the test times out. The skip below could not
     fire because the await above it never resolved. Count first; the count is immediate. */
  const signIn = page.getByRole("link", { name: /^sign in$/i }).first();
  const hasAccountControls = (await signIn.count()) > 0;
  test.skip(!hasAccountControls, "Clerk is not configured here, so there is no group to join");

  const wordmark = await page.getByRole("link", { name: /chessmark home/i }).boundingBox();
  const button = await trigger.boundingBox();
  const account = await signIn.boundingBox();

  /* Asserted rather than returned early. `if (!box) return` reads like a guard and behaves like a
     pass: every one of these boxes must exist for the measurement below to mean anything, and a
     silent return would make this test green against the very layout it was written for. */
  expect(wordmark, "the wordmark should have a box").not.toBeNull();
  expect(button, "the trigger should have a box").not.toBeNull();
  expect(account, "the account controls should have a box").not.toBeNull();

  /**
   * **All the free space belongs on the wordmark's side.**
   *
   * The trigger should sit one flex gap from the account controls — they are one group — with
   * every spare pixel in the bar pushed in front of them. When it was broken the two auto margins
   * split that space evenly instead, leaving the trigger stranded in the middle.
   *
   * Asserted as a *ratio*, and that detail is load-bearing. The obvious form —
   * `toAccount < toWordmark` — is green against the broken layout: splitting the space evenly makes
   * the two gaps equal, and they came out 29.219 and 29.234, so the comparison passed on sixteen
   * thousandths of a pixel. Correct, the gaps are 8 and 28. Doubling separates the two cases by a
   * wide margin and cannot be won by rounding.
   */
  const toAccount = account!.x - (button!.x + button!.width);
  const toWordmark = button!.x - (wordmark!.x + wordmark!.width);
  expect(
    toWordmark,
    `the spare width should sit before the trigger, not around it ` +
      `(${toWordmark.toFixed(1)}px before, ${toAccount.toFixed(1)}px after)`,
  ).toBeGreaterThan(toAccount * 2);
});

test("at phone width every header control is the same height", async ({ page }) => {
  /**
   * The bar had three heights in it.
   *
   * Each control derived its own from padding plus whatever it happened to contain: the nav
   * trigger came to 28px from a 14px glyph, `sign in` to 26.5px from its line-height, and the
   * signed-in account card to 32px from a 22px avatar. Side by side on a phone that is plainly
   * visible, and no amount of care about any one of them fixes it — the heights have to be stated
   * once, which is what `CONTROL_HEIGHT` is for.
   *
   * Measured rather than asserted against a constant: what matters is that they agree with each
   * other, not that they agree with a number this test also hard-codes.
   */
  await page.goto("/");

  const boxes = await page
    .locator("header a[class*='border'], header button[class*='border']")
    .evaluateAll((els) =>
      els
        .map((el) => el.getBoundingClientRect())
        .filter((r) => r.height > 0)
        .map((r) => ({ h: Math.round(r.height), top: Math.round(r.top) })),
    );

  test.skip(boxes.length < 2, "only one bordered control here — nothing to compare");

  const heights = [...new Set(boxes.map((b) => b.h))];
  expect(heights, `header controls disagree on height: ${JSON.stringify(boxes)}`).toHaveLength(1);

  // And they sit on the same line, which is the thing a reader actually notices.
  const tops = [...new Set(boxes.map((b) => b.top))];
  expect(tops, `header controls are not aligned: ${JSON.stringify(boxes)}`).toHaveLength(1);
});

/**
 * The lobby's podium, at 390px.
 *
 * Three columns of 113px is where a podium stops working, and it was stacked into a plain ranked
 * list here until the numbers were measured. It fits — but only because two figures per card wait
 * for `sm` and the name is allowed to wrap to three lines. Both are the kind of decision a later
 * tidy-up undoes, so both are asserted: the plinths still descend, and nothing is cut off.
 */
test("the podium is a podium on a phone, and nothing on it is cut off", async ({ page }) => {
  await page.goto("/");

  const places = page.getByRole("list", { name: "The top three" }).locator("> li");
  const count = await places.count();
  // Counted before measuring: `boundingBox()` waits, so an absent podium would hang, not skip.
  test.skip(count < 3, "fewer than three ranked contestants in this database");

  const rows = await places.evaluateAll((items) =>
    items.map((item) => {
      const box = item.getBoundingClientRect();
      const name = item.querySelector("p")!;
      return {
        top: Math.round(box.y),
        floor: Math.round(box.bottom),
        /* The name wraps rather than truncating, so nothing of it is behind a `title` a thumb
           cannot open — the failure the nameplate fix on the game page was about. Height as well
           as width: it is clamped to three lines, which every production name fits and a longer
           one would not. */
        clipped:
          name.scrollWidth > name.clientWidth + 1 || name.scrollHeight > name.clientHeight + 1,
      };
    }),
  );

  // The DOM order is the ranking, so this is first, second, third whatever the painted order is.
  expect(rows[0].top, "first place should stand highest, here too").toBeLessThan(rows[1].top);
  expect(rows[1].top, "second place above third").toBeLessThan(rows[2].top);
  expect(new Set(rows.map((row) => row.floor)).size, "on one floor").toBe(1);

  for (const row of rows) {
    expect(row.clipped, "the model name should wrap, not be cut off").toBe(false);
  }
});

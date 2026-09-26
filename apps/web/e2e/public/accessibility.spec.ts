import { expect, test, type Page } from "@playwright/test";

import { fixtures } from "../fixtures";

/**
 * Four accessibility rules, asserted as the structure that breaks them.
 *
 * Each was found by Lighthouse against production's data and was invisible in the suite's own:
 * the seeded tournament has no departed models, and nothing opened the prose pages looking for
 * links. Lighthouse runs a fixed handful of URLs; these run on every public page the suite knows,
 * and each asserts the property itself rather than a score that can absorb one failure.
 */

async function pages(page: Page): Promise<string[]> {
  const { replayGame, tournament, decisionGame } = fixtures();
  await page.goto("/models");
  const model = await page.locator('a[href^="/models/"]').first().getAttribute("href");
  return [
    "/",
    "/about",
    "/methodology",
    "/leaderboard",
    "/games",
    "/games?q=zz-no-such-player-zz",
    "/models",
    "/tournaments",
    "/play",
    "/sign-in",
    "/sign-up",
    `/games/${replayGame}`,
    // A decision game draws its turns differently — bars, a ranked list, a toggle (ADR-0049).
    `/games/${decisionGame}`,
    ...(tournament ? [`/tournaments/${tournament}`] : []),
    ...(model ? [model] : []),
  ];
}

test("a link inside a sentence is underlined, not told apart by colour alone", async ({ page }) => {
  /* WCAG 1.4.1. The accent is 1.2:1 against the prose around it, so a gold link that underlines
     only on hover is invisible as a link to anyone who cannot tell the two colours apart. */
  for (const path of await pages(page)) {
    await page.goto(path);
    const bare = await page.locator("p a, li > p a").evaluateAll((links) =>
      links
        .filter((link) => {
          const block = link.closest("p")!;
          // A link that *is* the paragraph has no surrounding text to be confused with.
          const own = (link.textContent ?? "").trim();
          const around = (block.textContent ?? "").replace(own, "").replace(/[\s.·→,]/g, "");
          return around.length > 0;
        })
        .filter((link) => !getComputedStyle(link).textDecorationLine.includes("underline"))
        .map((link) => link.outerHTML.slice(0, 120)),
    );
    expect(bare, `${path}: links in running text without an underline`).toEqual([]);
  }
});

test("a definition list holds only terms and descriptions", async ({ page }) => {
  /* A `<p>` for a stat's note made every `<dl>` invalid, and a screen reader announces a `<dl>`
     as term/description pairs — the note fell outside them. */
  for (const path of await pages(page)) {
    await page.goto(path);
    const strays = await page.locator("dl").evaluateAll((lists) =>
      lists.flatMap((list) =>
        [...list.children].flatMap((child) => {
          const items = child.tagName === "DIV" ? [...child.children] : [child];
          return items
            .filter((item) => !["DT", "DD", "SCRIPT", "TEMPLATE"].includes(item.tagName))
            .map((item) => item.outerHTML.slice(0, 100));
        }),
      ),
    );
    expect(strays, `${path}: elements a <dl> may not contain`).toEqual([]);
  }
});

test("an accessible name is only given to an element that can carry one", async ({ page }) => {
  /* `aria-label` on a plain `<i>` or `<span>` is prohibited — it has no role to name — and is
     dropped rather than read. The tournament schedule's state dots said what they meant to
     nobody. */
  for (const path of await pages(page)) {
    await page.goto(path);
    const unnamed = await page
      .locator("i[aria-label], span[aria-label], div[aria-label]")
      .evaluateAll((elements) =>
        elements.filter((el) => !el.getAttribute("role")).map((el) => el.outerHTML.slice(0, 100)),
      );
    expect(unnamed, `${path}: aria-label with no role to name`).toEqual([]);
  }
});

test("nothing that can be read is faded with opacity", async ({ page }) => {
  /* `ink-faint` is the faintest text that passes AA; `opacity-50` on top of it measured 2.5:1 on a
     pool's departed rows. Asserted as the effective opacity of every element with its own text,
     because the dimming was applied to the row and inherited by everything inside it. */
  for (const path of await pages(page)) {
    await page.goto(path);
    const faded = await page.evaluate(() => {
      const found: string[] = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const el = node.parentElement;
        if (!el || !(node.textContent ?? "").trim() || el.closest("[aria-hidden='true']")) continue;
        if (!el.checkVisibility()) continue;
        let opacity = 1;
        for (let at: Element | null = el; at; at = at.parentElement) {
          opacity *= Number(getComputedStyle(at).opacity);
        }
        if (opacity < 0.99) found.push(`${opacity.toFixed(2)} ${el.outerHTML.slice(0, 90)}`);
      }
      return found;
    });
    expect(faded, `${path}: text rendered at reduced opacity`).toEqual([]);
  }
});

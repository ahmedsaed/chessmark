import { expect, test } from "@playwright/test";

/**
 * Reaching the auth screens by **clicking**, which is how a person reaches them.
 *
 * The bug this file exists for shipped and reached a person. The root layout mounts `ClerkProvider`
 * from the request path and the session cookie, and the App Router preserves a shared layout across
 * a client-side navigation — so a signed-out reader on `/` got no provider, clicked `sign in`, and
 * `useSignIn` threw `useClerkSignal can only be used within the <ClerkProvider /> component`. The
 * root error boundary rendered "That did not load." over the site's own sign-in button.
 *
 * **Nothing caught it because every other test uses `page.goto`.** A document request re-renders the
 * root layout, which is the one path where the decision is correct. `auth.setup.ts` even navigates
 * straight to `/sign-in` and says why. The whole class of bug lives in the gap between the two, so
 * these tests click and assert that it was a *soft* navigation — a test that silently fell back to
 * a full load would pass against the broken build it was written for.
 *
 * Skipped loudly without Clerk: with no publishable key `AccountBar` renders nothing, so there is
 * no link to click and the test would prove nothing. The same bargain the signed-in project strikes.
 */

/** Set before clicking; a full document load wipes it. This is what makes the click meaningful. */
const MARKER = "__chessmark_soft_nav__";

async function clickAndProveItWasSoft(
  page: import("@playwright/test").Page,
  name: RegExp,
): Promise<void> {
  await page.evaluate((key) => {
    (window as unknown as Record<string, unknown>)[key] = true;
  }, MARKER);

  await page.getByRole("link", { name }).first().click();

  const survived = await page.evaluate(
    (key) => Boolean((window as unknown as Record<string, unknown>)[key]),
    MARKER,
  );
  expect(survived, "this must be a client-side navigation, or the test proves nothing").toBe(true);
}

for (const [label, from, link, heading] of [
  ["sign in", "/", /^sign in$/i, /sign in/i],
  ["sign up", "/leaderboard", /^sign up$/i, /create an account/i],
] as const) {
  test(`clicking ${label} from a page with no Clerk reaches a working form`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await page.goto(from);

    const trigger = page.getByRole("link", { name: link }).first();
    test.skip(!(await trigger.isVisible().catch(() => false)), "Clerk is not configured here");

    // The precondition, asserted rather than assumed: this page must have *no* Clerk on it, or the
    // navigation below is not the one that used to break.
    expect(
      await page.evaluate(() => typeof (window as { Clerk?: unknown }).Clerk !== "undefined"),
      `${from} should carry no Clerk — that is the 87 KiB this whole design buys`,
    ).toBe(false);

    await clickAndProveItWasSoft(page, link);

    // The form, not the error boundary. Asserted on the heading *and* a field, because
    // "That did not load." is also an <h1> and a page with a heading and no form is not a form.
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByLabel(/email/i).first()).toBeVisible();

    expect(
      errors.filter((error) => /ClerkProvider|ClerkSignal/.test(error)),
      "a Clerk hook ran without its provider",
    ).toEqual([]);
  });
}

/**
 * The fix must not be "mount Clerk everywhere", which would work and cost 87 KiB on every route.
 *
 * `clerk.browser.js` is the bundle in question. A reader who never goes near an identity route must
 * never fetch it; one who clicks through to `/sign-in` must.
 */
test("Clerk is fetched when a navigation needs it, and not before", async ({ page }) => {
  const fetched: string[] = [];
  page.on("request", (request) => {
    if (/clerk\.browser\.js/.test(request.url())) fetched.push(request.url());
  });

  await page.goto("/leaderboard");
  await expect(page.getByRole("heading", { level: 1, name: /leaderboard/i })).toBeVisible();

  const trigger = page.getByRole("link", { name: /^sign in$/i }).first();
  test.skip(!(await trigger.isVisible().catch(() => false)), "Clerk is not configured here");

  expect(fetched, "a signed-out reader on /leaderboard must not download Clerk").toEqual([]);

  await clickAndProveItWasSoft(page, /^sign in$/i);
  await expect(page.getByRole("heading", { level: 1, name: /sign in/i })).toBeVisible();

  expect(fetched.length, "clicking through to /sign-in must load Clerk").toBeGreaterThan(0);
});

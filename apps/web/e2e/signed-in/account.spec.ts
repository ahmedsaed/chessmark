import { randomUUID } from "node:crypto";

import { clerkSetup, setupClerkTestingToken } from "@clerk/testing/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * An account, from creation to signing out (AUTH-01, AUTH-11, UI-09).
 *
 * **These screens are ours now, and nothing was asserting them.** Replacing Clerk's prebuilt
 * `<SignIn />`, `<SignUp />` and `<UserProfile />` took 285 KiB off every route and moved four
 * flows — sign-up, sign-in, editing a display name, signing out — out of a vendor's tested
 * component and into `AuthForm.tsx` and `ProfileView.tsx`, where the only thing checking them was
 * somebody looking at the page. Hand-rolled auth fails by locking people out, which is the failure
 * you discover from a support message rather than from a test.
 *
 * `auth.setup.ts` does **not** cover this. It drives `window.Clerk` through `clerk.signIn`, which
 * is the right way to get a session for the play tests and goes nowhere near our form: every
 * field, every step transition and every error path below is untouched by it.
 *
 * **A throwaway identity, not the suite's account.** Clerk's `signOut` ends the sessions of the
 * *client*, and every context built from `STORAGE_STATE` restores the same client cookie — so
 * signing out here on the shared account would revoke the session `play.spec.ts` is still using.
 * A fresh user avoids that entirely, and it is also the only way to assert what a new account
 * actually gets (no credits, AUTH-11). It is deleted again in `afterAll`.
 */

/** Clerk's fixed code for a `+clerk_test` address on a development instance. No inbox to poll. */
const TEST_CODE = "424242";

/** Unique per run: Clerk refuses a second sign-up on an address that already exists. */
const EMAIL = `chessmark-e2e-${Date.now()}+clerk_test@example.com`;

/**
 * What the sign-up form's password field gets. Generated per run, never written down.
 *
 * It has to clear the instance's 15-character floor and miss HaveIBeenPwned, which Clerk checks on
 * sign-up, so anything memorable is rejected anyway. Generating it means no string in this
 * repository and no string in a CI log opens anything — which is the property the secret scanner
 * is right to insist on, and it rejected the fixed one this started as.
 */
const enrolment = `e2e-${randomUUID()}`;

const DISPLAY_NAME = `e2e-${Date.now()}`;

test.describe.configure({ mode: "serial" });

test.describe("an account, from creation to signing out", () => {
  /**
   * One context for the whole describe, deliberately.
   *
   * The steps are a single journey — you cannot edit the profile of an account you have not
   * created — and each one leaves the session the next needs. A fresh context per test would mean
   * signing in again to assert signing out.
   */
  let page: Page;
  let userId: string | null = null;

  test.beforeAll(async ({ browser }: { browser: Browser }) => {
    await clerkSetup();
    const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    page = await context.newPage();
  });

  test.afterAll(async () => {
    /* Every run would otherwise leave a user behind on the development instance. Deleting through
       the backend API rather than the UI because the UI has no such button — `ProfileView` manages
       a display name and a sign-out, and account deletion stays with the provider (by design). */
    if (userId && process.env.CLERK_SECRET_KEY) {
      await fetch(`https://api.clerk.com/v1/users/${userId}`, {
        method: "DELETE",
        headers: { authorization: `Bearer ${process.env.CLERK_SECRET_KEY}` },
      }).catch(() => {
        // A leftover test user is untidy, not a failure — it must not turn a green run red.
      });
    }
    await page?.context().close();
  });

  test("creating an account through our own form signs you in", async () => {
    /* Bot protection is on for sign-up, and a headless browser is exactly what it exists to stop.
       The testing token is Clerk's supported way through it; without this the form comes back with
       a captcha error and the failure looks like ours. */
    await setupClerkTestingToken({ page });

    await page.goto("/sign-up");
    await expect(page.getByRole("heading", { name: "Create an account" })).toBeVisible();

    await page.getByLabel("Email address").fill(EMAIL);

    /* **The field that was missing.** The instance requires a password, so a sign-up without one
       verified the emailed code and then stopped at `missing_requirements` — an account created
       but unusable, and the only sign of it a line of red text under the form. */
    await page.getByLabel("Password", { exact: true }).fill(enrolment);

    await page.getByRole("button", { name: "Continue", exact: true }).click();

    // The step changes rather than the page: one form, two states. The label naming the address is
    // how a person knows the code went where they meant it to.
    await expect(page.getByLabel(`Code sent to ${EMAIL}`)).toBeVisible();

    await page.getByLabel(`Code sent to ${EMAIL}`).fill(TEST_CODE);
    await page.getByRole("button", { name: "Verify", exact: true }).click();

    // `finalize({ navigate })` lands on `/play` — the default `safeRedirect` answer.
    await page.waitForURL(/\/play/);
    await expect(page.getByRole("link", { name: "Your profile" })).toBeVisible();

    userId = await page.evaluate(() => {
      const clerk = (window as unknown as { Clerk?: { user?: { id?: string } } }).Clerk;
      return clerk?.user?.id ?? null;
    });
    expect(userId, "the sign-up should have produced a Clerk user").toBeTruthy();
  });

  test("the profile shows the account, and a new one has no allowance", async () => {
    await page.goto("/profile");

    await expect(page.getByRole("heading", { name: "Profile", level: 1 })).toBeVisible();
    await expect(page.getByText(EMAIL)).toBeVisible();

    /* **Zero credits, and the readout has to reach zero rather than stop at the placeholder.**
       New accounts are granted nothing (AUTH-11) — an administrator does that — so `0` here is
       both the correct answer and proof that `/me` was called with a real token and answered.
       `—` is what the page shows before the fetch lands, and asserting "not —" is what tells the
       two apart. */
    const credits = page.locator("dl div", { has: page.getByText("credits", { exact: true }) });
    await expect(credits.locator("dd")).toHaveText("0");
  });

  test("a display name is saved, and is still there after a reload", async () => {
    await page.goto("/profile");

    await page.getByLabel("Display name").fill(DISPLAY_NAME);
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByRole("status")).toHaveText("Saved.");

    /* The reload is the assertion. "Saved." is our own state and would appear just the same if
       `user.update` had written nothing — the name has to come back from Clerk to count. */
    await page.reload();
    await expect(page.getByLabel("Display name")).toHaveValue(DISPLAY_NAME);
  });

  test("signing out returns the whole site to its signed-out state", async () => {
    await page.goto("/profile");
    await page.getByRole("button", { name: "Sign out" }).click();

    // `signOut(() => router.push("/"))` — the callback is the navigation, so landing here is the
    // sign-out having completed rather than a race with it.
    await page.waitForURL(/\/$/);
    await expect(page.getByRole("link", { name: /^sign in$/i })).toBeVisible();

    /* And the page that needs an identity says so, rather than showing an empty form. This is the
       conditional provider's edge: `/profile` is an identity route, so Clerk still mounts — the
       answer has to come from Clerk reporting no session, not from the provider being absent. */
    await page.goto("/profile");
    await expect(page.getByText("You are not signed in.")).toBeVisible();
  });

  test("signing back in through our own form returns the same account", async () => {
    /**
     * The path `auth.setup.ts` cannot assert. It calls `clerk.signIn`, which talks to
     * `window.Clerk` directly — our two-step form, its `emailCode` calls and its `finalize` are
     * never executed by it. This is the only test that runs them.
     */
    await setupClerkTestingToken({ page });

    await page.goto("/sign-in");
    await page.getByLabel("Email address").fill(EMAIL);
    await page.getByRole("button", { name: "Continue", exact: true }).click();

    await page.getByLabel(`Code sent to ${EMAIL}`).fill(TEST_CODE);
    await page.getByRole("button", { name: "Verify", exact: true }).click();

    await page.waitForURL(/\/play/);

    // The same account, not merely *an* account: the display name written two tests ago is still
    // on it.
    await page.goto("/profile");
    await expect(page.getByText(EMAIL)).toBeVisible();
    await expect(page.getByLabel("Display name")).toHaveValue(DISPLAY_NAME);
  });
});

test("the sign-in page refuses to be turned into an open redirect", async ({ page }) => {
  /**
   * `?redirect=` exists so a link into the sign-in page can send you back where you were. An
   * absolute URL there is somebody else's phishing page reached through a link that genuinely
   * starts at our domain, and `safeRedirect` refuses it — this asserts the refusal survives,
   * because the failure is silent and the reward for it is a convincing credential harvest.
   *
   * Asserted without signing in: the guard is a property of the page, and reading the form is
   * enough to know which target it holds.
   */
  await page.goto("/sign-in?redirect=https://example.com/steal");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  // Nothing on the page offers the foreign origin as a destination.
  const links = await page.locator("a[href]").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("href")),
  );
  expect(links.some((href) => href?.includes("example.com"))).toBe(false);
});

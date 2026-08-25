import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

import { clerk, clerkSetup } from "@clerk/testing/playwright";
import { expect, test as setup } from "@playwright/test";

import { STORAGE_STATE } from "../playwright.config";

export const E2E_EMAIL = "chessmark-e2e+clerk_test@example.com";

/**
 * Signs the suite's test account in, for real, and gives it credits to spend.
 *
 * This is a genuine Clerk sign-in against a real development instance, producing a real session
 * JWT that the API verifies against real JWKS with the algorithm pinned. Nothing about auth is
 * stubbed — a shim here would leave the one security-relevant path in the product untested, and
 * the JIT provisioning bug (`/me` answered correctly while `users` stayed empty) is exactly the
 * kind of thing only the real path catches.
 *
 * The address ends `+clerk_test@example.com`, which a Clerk development instance treats as a test
 * identity: no mail is sent and the code is fixed. So there is no password in this repository and
 * no inbox to poll.
 */
setup("sign in", async ({ page }) => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");

  await clerkSetup();

  await page.goto("/");
  await clerk.signIn({ page, signInParams: { strategy: "email_code", identifier: E2E_EMAIL } });

  await page.goto("/play");
  // The account bar renders only once Clerk has loaded a session, and it is what calls `/me` —
  // which is what provisions the user row the credit grant below needs.
  await expect(page.getByRole("button", { name: /sign in/i })).toHaveCount(0);

  // Now that the row exists, top the balance up. New users deliberately get none (AUTH-11), so
  // without this the suite could not start a game.
  const stdout = execFileSync("uv", ["run", "python", "../../scripts/seed_e2e_user.py"], {
    cwd: path.join(repoRoot, "apps", "api"),
    encoding: "utf8",
    env: { ...process.env, PATH: `${process.env.HOME}/.local/bin:${process.env.PATH}` },
  });
  const account = JSON.parse(stdout) as { credits: number | null };
  expect(account.credits, "the test account should have been provisioned and funded").toBeGreaterThan(0);

  mkdirSync(path.dirname(STORAGE_STATE), { recursive: true });
  await page.context().storageState({ path: STORAGE_STATE });
});

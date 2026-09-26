import { expect, test } from "@playwright/test";

import { fixtures } from "../fixtures";

/**
 * A game between two decision models (ADR-0049): Fool's Mate, played by two scripted decision
 * seats through the real worker. Every turn is one decision rather than reasoning and tool calls,
 * and the page has to draw that — including the move each seat weighed highest.
 */

test("a decision turn shows how the model weighed its moves", async ({ page }) => {
  const { decisionGame } = fixtures();
  await page.goto(`/games/${decisionGame}`);

  // The newest turn is open: Black's mating Qh4, ranked first of the moves it was offered.
  const decision = page.getByTestId("decision").last();
  await expect(decision).toBeVisible();
  await expect(decision).toContainText("decided among");
  const ranking = decision.getByRole("list", { name: "How the model weighed its moves" });
  await expect(ranking.getByRole("listitem").first()).toContainText("Qh4");
  await expect(ranking.getByRole("listitem").first()).toContainText("(chosen)");
});

test("both seats say they are decision models", async ({ page }) => {
  const { decisionGame } = fixtures();
  await page.goto(`/games/${decisionGame}`);
  await expect(page.getByText("decision", { exact: true })).toHaveCount(2);
});

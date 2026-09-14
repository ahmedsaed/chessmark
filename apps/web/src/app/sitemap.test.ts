import { beforeEach, describe, expect, it, vi } from "vitest";

import { siteUrl, staticRoutes } from "@/lib/site";

const listGames = vi.fn();
const listModels = vi.fn();
const listTournaments = vi.fn();

vi.mock("@/lib/api", () => ({
  listGames: (...args: unknown[]) => listGames(...args),
  listModels: (...args: unknown[]) => listModels(...args),
  listTournaments: (...args: unknown[]) => listTournaments(...args),
}));

const { default: sitemap } = await import("@/app/sitemap");

beforeEach(() => {
  listGames.mockResolvedValue([]);
  listModels.mockResolvedValue([]);
  listTournaments.mockResolvedValue([]);
});

async function urls(): Promise<string[]> {
  return (await sitemap()).map((entry) => entry.url);
}

describe("sitemap", () => {
  it("lists every static route", async () => {
    const listed = await urls();
    for (const route of staticRoutes) {
      expect(listed).toContain(`${siteUrl}${route.path === "/" ? "" : route.path}`);
    }
  });

  /**
   * The regression. `/tournaments` was in both navs and in neither the sitemap nor any test.
   * This fails against the sitemap as it stood before ADR-0043's page was added here.
   */
  it("lists the tournaments index", async () => {
    expect(await urls()).toContain(`${siteUrl}/tournaments`);
  });

  it("lists a page for each tournament", async () => {
    listTournaments.mockResolvedValue([{ slug: "opening-cup" }, { slug: "winter-swiss" }]);
    const listed = await urls();
    expect(listed).toContain(`${siteUrl}/tournaments/opening-cup`);
    expect(listed).toContain(`${siteUrl}/tournaments/winter-swiss`);
  });

  it("lists a page for each model", async () => {
    listModels.mockResolvedValue([{ openrouter_id: "openai/gpt-4o" }]);
    expect(await urls()).toContain(`${siteUrl}/models/openai/gpt-4o`);
  });

  /* A running game's content changes every few seconds and its URL is only worth indexing once
     there is a result to read. Listing one invites a crawl of a page that will never match. */
  it("lists finished games and omits running ones", async () => {
    listGames.mockResolvedValue([
      { id: "done", status: "finished", ended_at: "2026-01-02T03:04:05Z" },
      { id: "live", status: "running", ended_at: null },
      { id: "gone", status: "aborted", ended_at: "2026-01-02T03:04:05Z" },
    ]);
    const listed = await urls();
    expect(listed).toContain(`${siteUrl}/games/done`);
    expect(listed).not.toContain(`${siteUrl}/games/live`);
    expect(listed).not.toContain(`${siteUrl}/games/gone`);
  });

  it("emits absolute urls under the site origin, which the spec requires", async () => {
    listGames.mockResolvedValue([{ id: "done", status: "finished", ended_at: null }]);
    listModels.mockResolvedValue([{ openrouter_id: "openai/gpt-4o" }]);
    listTournaments.mockResolvedValue([{ slug: "opening-cup" }]);
    for (const url of await urls()) {
      expect(url.startsWith(`${siteUrl}/`) || url === siteUrl).toBe(true);
    }
  });

  it("names no url twice", async () => {
    const listed = await urls();
    expect(new Set(listed).size).toBe(listed.length);
  });
});

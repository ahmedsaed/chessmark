import { beforeEach, describe, expect, it, vi } from "vitest";

import { siteUrl, staticRoutes } from "@/lib/site";

const listGames = vi.fn();
const listCatalogue = vi.fn();
const listTournaments = vi.fn();

vi.mock("@/lib/api", () => ({
  listGames: (...args: unknown[]) => listGames(...args),
  listCatalogue: (...args: unknown[]) => listCatalogue(...args),
  listTournaments: (...args: unknown[]) => listTournaments(...args),
}));

const { default: sitemap } = await import("@/app/sitemap");

beforeEach(() => {
  listGames.mockResolvedValue([]);
  listCatalogue.mockResolvedValue([]);
  listTournaments.mockResolvedValue([]);
});

function played(openrouter_id: string, last_played_at = "2026-10-01T00:00:00Z") {
  return { openrouter_id, last_played_at };
}

function event(slug: string, last_game_at: string | null = null) {
  return { slug, stats: { last_game_at } };
}

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
    listTournaments.mockResolvedValue([event("opening-cup"), event("winter-swiss")]);
    const listed = await urls();
    expect(listed).toContain(`${siteUrl}/tournaments/opening-cup`);
    expect(listed).toContain(`${siteUrl}/tournaments/winter-swiss`);
  });

  it("lists a page for each model that has played", async () => {
    listCatalogue.mockResolvedValue([played("openai/gpt-4o")]);
    expect(await urls()).toContain(`${siteUrl}/models/openai/gpt-4o`);
  });

  /* The regression. 272 of 299 model pages were for models that had never played — the same
     template each — and Search Console left 447 of the site's pages "Discovered – currently not
     indexed". The page says `noindex` for these, so listing one here is also a contradiction. */
  it("omits a model that has never finished a game", async () => {
    listCatalogue.mockResolvedValue([
      { openrouter_id: "vendor/fresh", last_played_at: null },
      { openrouter_id: "vendor/older-api" },
    ]);
    const listed = await urls();
    expect(listed).not.toContain(`${siteUrl}/models/vendor/fresh`);
    expect(listed).not.toContain(`${siteUrl}/models/vendor/older-api`);
  });

  it("dates a model by its latest game and an event by its latest game", async () => {
    listCatalogue.mockResolvedValue([played("openai/gpt-4o", "2026-10-01T12:00:00Z")]);
    listTournaments.mockResolvedValue([event("opening-cup", "2026-10-02T08:00:00Z")]);
    const entries = await sitemap();
    const at = (url: string) => entries.find((entry) => entry.url === url)?.lastModified;
    expect(at(`${siteUrl}/models/openai/gpt-4o`)).toEqual(new Date("2026-10-01T12:00:00Z"));
    expect(at(`${siteUrl}/tournaments/opening-cup`)).toEqual(new Date("2026-10-02T08:00:00Z"));
  });

  it("omits a game shorter than ten plies", async () => {
    listGames.mockResolvedValue([
      { id: "short", status: "finished", ply_count: 9, ended_at: null },
      { id: "long", status: "finished", ply_count: 10, ended_at: null },
    ]);
    const listed = await urls();
    expect(listed).not.toContain(`${siteUrl}/games/short`);
    expect(listed).toContain(`${siteUrl}/games/long`);
  });

  /* A running game's content changes every few seconds and its URL is only worth indexing once
     there is a result to read. Listing one invites a crawl of a page that will never match. */
  it("lists finished games and omits running ones", async () => {
    listGames.mockResolvedValue([
      { id: "done", status: "finished", ply_count: 40, ended_at: "2026-01-02T03:04:05Z" },
      { id: "live", status: "running", ply_count: 40, ended_at: null },
      { id: "gone", status: "aborted", ply_count: 40, ended_at: "2026-01-02T03:04:05Z" },
    ]);
    const listed = await urls();
    expect(listed).toContain(`${siteUrl}/games/done`);
    expect(listed).not.toContain(`${siteUrl}/games/live`);
    expect(listed).not.toContain(`${siteUrl}/games/gone`);
  });

  it("emits absolute urls under the site origin, which the spec requires", async () => {
    listGames.mockResolvedValue([
      { id: "done", status: "finished", ply_count: 40, ended_at: null },
    ]);
    listCatalogue.mockResolvedValue([played("openai/gpt-4o")]);
    listTournaments.mockResolvedValue([event("opening-cup")]);
    for (const url of await urls()) {
      expect(url.startsWith(`${siteUrl}/`) || url === siteUrl).toBe(true);
    }
  });

  it("names no url twice", async () => {
    const listed = await urls();
    expect(new Set(listed).size).toBe(listed.length);
  });
});

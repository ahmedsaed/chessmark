import { describe, expect, it } from "vitest";

import { footerNav, pageMetadata, primaryNav, siteUrl, staticRoutes } from "@/lib/site";

describe("staticRoutes", () => {
  /**
   * The regression this exists for: `/tournaments` shipped with ADR-0043, went into both navs,
   * and never reached the sitemap. Nothing compared the two lists, so the page was unlisted for
   * the whole life of the feature. Delete `/tournaments` from `staticRoutes` and this fails.
   */
  it("lists every route the site links to in its own footer", () => {
    const declared = new Set(staticRoutes.map((route) => route.path));
    const missing = footerNav.map((link) => link.href).filter((href) => !declared.has(href));
    expect(missing).toEqual([]);
  });

  it("lists every route in the header's primary navigation", () => {
    const declared = new Set(staticRoutes.map((route) => route.path));
    const missing = primaryNav.map((link) => link.href).filter((href) => !declared.has(href));
    expect(missing).toEqual([]);
  });

  it("names each route once", () => {
    const paths = staticRoutes.map((route) => route.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("gives the root the highest priority, since it is what a bare domain resolves to", () => {
    const root = staticRoutes.find((route) => route.path === "/");
    expect(root?.priority).toBe(1);
  });
});

describe("pageMetadata", () => {
  const leaderboard = pageMetadata({
    title: "Leaderboard",
    description: "Glicko-2 ratings.",
    path: "/leaderboard",
  });

  /**
   * The bug this prevents is silent: metadata keys are inherited wholesale, so before this helper
   * every page carried the root layout's OpenGraph block and every social card described the site
   * root instead of the page being shared.
   */
  it("gives the page its own social card rather than the site's", () => {
    expect(leaderboard.openGraph.title).toBe("Leaderboard — Chessmark");
    expect(leaderboard.openGraph.description).toBe("Glicko-2 ratings.");
    expect(leaderboard.twitter.title).toBe("Leaderboard — Chessmark");
  });

  /**
   * **The bug the helper above introduced while fixing the one above that.** Setting `openGraph`
   * replaces the parent's whole block, and the root's `opengraph-image.tsx` injects its card
   * *through* that block — so every page using this helper shipped with no `og:image` at all.
   * Seven routes unfurled as bare text for months. `/sign-in` sets no metadata and kept its card,
   * which is what identified the mechanism.
   */
  it("keeps a social card instead of inheriting none", () => {
    expect(leaderboard.openGraph.images).toEqual([
      { url: `${siteUrl}/opengraph-image`, width: 1200, height: 630 },
    ]);
    expect(leaderboard.twitter.images).toEqual([`${siteUrl}/opengraph-image`]);
  });

  /* A route that colocates an `opengraph-image` has the better card and the file outranks this
     anyway; naming both would put two `og:image` tags on one page and let the unfurler choose. */
  it("stands aside for a route that draws its own", () => {
    const own = pageMetadata({
      title: "Leaderboard",
      description: "Glicko-2 ratings.",
      path: "/leaderboard",
      hasOwnImage: true,
    });

    expect(own.openGraph.images).toBeUndefined();
    expect(own.twitter.images).toBeUndefined();
  });

  it("points the canonical at the page itself, never at the site root", () => {
    expect(leaderboard.alternates.canonical).toBe("/leaderboard");
  });

  it("builds an absolute OpenGraph url", () => {
    expect(leaderboard.openGraph.url).toBe(`${siteUrl}/leaderboard`);
  });

  /* `/` would otherwise concatenate to a `https://host/` with a trailing slash, which is a second
     spelling of the same URL for anything that compares them as strings. */
  it("does not leave a trailing slash on the root's url", () => {
    const root = pageMetadata({ title: "Watch", description: "Live games.", path: "/" });
    expect(root.openGraph.url).toBe(siteUrl);
  });
});

describe("primaryNav", () => {
  function active(pathname: string): string[] {
    return primaryNav.filter((link) => link.match?.(pathname)).map((link) => link.label);
  }

  /* The header highlights a link when its `match` fires. Nested routes have to light up their
     section — landing on a game's model page with nothing highlighted reads as having left the
     site. */
  it("lights a section up from its nested routes", () => {
    expect(active("/leaderboard")).toEqual(["Leaderboard"]);
    expect(active("/models/openai/gpt-4o")).toEqual(["Models"]);
    expect(active("/tournaments/opening-cup")).toEqual(["Tournaments"]);
    expect(active("/about")).toEqual(["About"]);
    expect(active("/play")).toEqual(["Play"]);
  });

  /* Never two at once: the bar would show the visitor in two places. */
  it("never lights more than one section", () => {
    for (const pathname of ["/", "/leaderboard", "/models/openai/gpt-4o", "/tournaments/x"]) {
      expect(active(pathname).length).toBeLessThanOrEqual(1);
    }
  });

  /* The root is the logo's job, not a nav link's — the header drops "Watch" for exactly that
     reason, and a lit-up link beside the logo would be the duplicate it was removed to avoid. */
  it("lights nothing on the lobby", () => {
    expect(active("/")).toEqual([]);
  });

  it("lights nothing on a game page, which belongs to no section", () => {
    expect(active("/games/abc")).toEqual([]);
  });
});

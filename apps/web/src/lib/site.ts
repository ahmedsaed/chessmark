/**
 * Site-level constants: the things the chrome, the metadata, and the sitemap must agree on.
 *
 * These lived nowhere before, which is why every page hand-rolled its own back-link and the
 * root URL had no OpenGraph card at all. One source, read by the header, the footer, and the
 * metadata routes.
 */

import { originFromEnv } from "@/lib/env";

/**
 * Absolute origin, needed by `metadataBase` and the sitemap — relative URLs are illegal there.
 *
 * `originFromEnv` rather than `??` for the reason recorded there: an unset build variable arrives
 * as `""`, and an empty `metadataBase` is a sitemap advertising relative URLs.
 */
export const siteUrl = originFromEnv(process.env.NEXT_PUBLIC_SITE_URL, "http://localhost:3010");

export const siteName = "Chessmark";

export const siteTagline = "Language models play chess. Everything is recorded.";

export const siteDescription =
  "LLM agents play chess through tools, against each other and against you. Every request, " +
  "reasoning trace, tool call, and taunt is stored, replayable, and one click from the " +
  "leaderboard number it produced.";

export interface NavLink {
  href: string;
  label: string;
  /** Matches nested routes too — `/games/:id` lights up "Watch". */
  match?: (pathname: string) => boolean;
}

/** The header's primary navigation. Kept short on purpose; everything else lives in the footer.
 *
 * No "Watch": it went to `/`, which the logo beside it already does — two controls one pixel apart
 * doing the same thing. The footer keeps it, where there is no logo to duplicate.
 */
export const primaryNav: NavLink[] = [
  { href: "/leaderboard", label: "Leaderboard", match: (p) => p.startsWith("/leaderboard") },
  { href: "/tournaments", label: "Tournaments", match: (p) => p.startsWith("/tournaments") },
  { href: "/models", label: "Models", match: (p) => p.startsWith("/models") },
  { href: "/play", label: "Play", match: (p) => p.startsWith("/play") },
  { href: "/about", label: "About", match: (p) => p.startsWith("/about") },
];

export const footerNav: NavLink[] = [
  { href: "/", label: "Watch" },
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/tournaments", label: "Tournaments" },
  { href: "/models", label: "Models" },
  { href: "/play", label: "Play" },
  { href: "/methodology", label: "Methodology" },
  { href: "/about", label: "About" },
];

/**
 * Every static route, with the crawl hints the sitemap needs.
 *
 * Declared here rather than inline in `sitemap.ts` because the two drifted: `/tournaments` shipped
 * with ADR-0043, went into both navs, and was never added to the sitemap — invisible, because
 * nothing compares the two lists. `sitemap.test.ts` now asserts every footer link appears here.
 */
export interface StaticRoute {
  path: string;
  changeFrequency: "hourly" | "daily" | "weekly" | "monthly" | "never";
  priority: number;
}

export const staticRoutes: StaticRoute[] = [
  { path: "/", changeFrequency: "hourly", priority: 1 },
  { path: "/leaderboard", changeFrequency: "daily", priority: 0.9 },
  { path: "/tournaments", changeFrequency: "daily", priority: 0.85 },
  { path: "/models", changeFrequency: "daily", priority: 0.8 },
  { path: "/play", changeFrequency: "monthly", priority: 0.7 },
  { path: "/methodology", changeFrequency: "monthly", priority: 0.6 },
  { path: "/about", changeFrequency: "monthly", priority: 0.6 },
];

/**
 * A page's own metadata: title, description, canonical, and a social card that names *this* page.
 *
 * Pages used to set a bare `title` and nothing else. Metadata keys are inherited wholesale, so
 * every one of them shared the root layout's OpenGraph block — sharing `/leaderboard` in Slack
 * produced the same card, word for word, as sharing the site root. The canonical is per-page for
 * the same reason, and must never move to the layout: inherited, it would mark the whole site a
 * duplicate of `/`.
 */
export function pageMetadata(page: { title: string; description: string; path: string }) {
  return {
    title: page.title,
    description: page.description,
    alternates: { canonical: page.path },
    openGraph: {
      title: `${page.title} — ${siteName}`,
      description: page.description,
      url: `${siteUrl}${page.path === "/" ? "" : page.path}`,
    },
    twitter: { title: `${page.title} — ${siteName}`, description: page.description },
  };
}

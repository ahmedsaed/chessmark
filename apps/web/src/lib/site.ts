/**
 * Site-level constants: the things the chrome, the metadata, and the sitemap must agree on.
 *
 * These lived nowhere before, which is why every page hand-rolled its own back-link and the
 * root URL had no OpenGraph card at all. One source, read by the header, the footer, and the
 * metadata routes.
 */

/** Absolute origin, needed by `metadataBase` and the sitemap — relative URLs are illegal there. */
export const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3010";

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

/** The header's primary navigation. Kept short on purpose; everything else lives in the footer. */
export const primaryNav: NavLink[] = [
  { href: "/", label: "Watch", match: (p) => p === "/" || p.startsWith("/games") },
  { href: "/leaderboard", label: "Leaderboard", match: (p) => p.startsWith("/leaderboard") },
  { href: "/play", label: "Play", match: (p) => p.startsWith("/play") },
  { href: "/about", label: "About", match: (p) => p.startsWith("/about") },
];

export const footerNav: NavLink[] = [
  { href: "/", label: "Watch" },
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/play", label: "Play" },
  { href: "/methodology", label: "Methodology" },
  { href: "/about", label: "About" },
];

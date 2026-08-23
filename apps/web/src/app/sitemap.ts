import type { MetadataRoute } from "next";

import { listGames } from "@/lib/api";
import { siteUrl } from "@/lib/site";

/* Built static by default, which would freeze the game list at deploy time. Hourly is the right
   cadence for a sitemap — crawlers do not need a game the second it finishes. */
export const revalidate = 3600;

/**
 * Static pages plus the most recent finished games.
 *
 * Running games are left out on purpose: their content changes every few seconds and their URL
 * is only interesting once there is a result to read.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const games = await listGames(undefined, 200);

  const statics: MetadataRoute.Sitemap = [
    { url: siteUrl, changeFrequency: "hourly", priority: 1 },
    { url: `${siteUrl}/leaderboard`, changeFrequency: "daily", priority: 0.9 },
    { url: `${siteUrl}/methodology`, changeFrequency: "monthly", priority: 0.6 },
    { url: `${siteUrl}/about`, changeFrequency: "monthly", priority: 0.6 },
  ];

  const finished = games
    .filter((game) => game.status === "finished")
    .map((game) => ({
      url: `${siteUrl}/games/${game.id}`,
      lastModified: game.ended_at ? new Date(game.ended_at) : undefined,
      changeFrequency: "never" as const,
      priority: 0.5,
    }));

  return [...statics, ...finished];
}

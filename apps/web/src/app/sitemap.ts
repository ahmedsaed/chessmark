import type { MetadataRoute } from "next";

import { listGames, listModels } from "@/lib/api";
import { siteUrl } from "@/lib/site";

/* Built static by default, which would freeze the game list at deploy time. Hourly is the right
   cadence for a sitemap — crawlers do not need a game the second it finishes. */
export const revalidate = 3600;

/**
 * Static pages, every model, and the most recent finished games.
 *
 * Running games are left out on purpose: their content changes every few seconds and their URL
 * is only interesting once there is a result to read. Model pages are the opposite — stable,
 * individually meaningful, and the thing a search for a model name should find.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [games, models] = await Promise.all([listGames(undefined, 200), listModels()]);

  const statics: MetadataRoute.Sitemap = [
    { url: siteUrl, changeFrequency: "hourly", priority: 1 },
    { url: `${siteUrl}/leaderboard`, changeFrequency: "daily", priority: 0.9 },
    { url: `${siteUrl}/models`, changeFrequency: "daily", priority: 0.8 },
    { url: `${siteUrl}/play`, changeFrequency: "monthly", priority: 0.7 },
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

  const catalogue: MetadataRoute.Sitemap = models.map((model) => ({
    url: `${siteUrl}/models/${model.openrouter_id}`,
    changeFrequency: "weekly" as const,
    priority: 0.4,
  }));

  return [...statics, ...catalogue, ...finished];
}

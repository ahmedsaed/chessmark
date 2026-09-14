import type { MetadataRoute } from "next";

import { listGames, listModels, listTournaments } from "@/lib/api";
import { siteUrl, staticRoutes } from "@/lib/site";

/* Built static by default, which would freeze the game list at deploy time. Hourly is the right
   cadence for a sitemap — crawlers do not need a game the second it finishes. */
export const revalidate = 3600;

/**
 * Static pages, every model, every tournament, and the most recent finished games.
 *
 * Running games are left out on purpose: their content changes every few seconds and their URL
 * is only interesting once there is a result to read. Model and tournament pages are the
 * opposite — stable, individually meaningful, and the thing a search for a model or an event
 * name should find.
 *
 * The static list lives in `lib/site.ts` so it cannot drift from the site's own navigation again;
 * `/tournaments` was missing here for the whole life of the feature.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [games, models, tournaments] = await Promise.all([
    listGames(undefined, 200),
    listModels(),
    listTournaments(100),
  ]);

  const statics: MetadataRoute.Sitemap = staticRoutes.map((route) => ({
    url: `${siteUrl}${route.path === "/" ? "" : route.path}`,
    changeFrequency: route.changeFrequency,
    priority: route.priority,
  }));

  const catalogue: MetadataRoute.Sitemap = models.map((model) => ({
    url: `${siteUrl}/models/${model.openrouter_id}`,
    changeFrequency: "weekly" as const,
    priority: 0.4,
  }));

  const events: MetadataRoute.Sitemap = tournaments.map((tournament) => ({
    url: `${siteUrl}/tournaments/${tournament.slug}`,
    changeFrequency: "daily" as const,
    priority: 0.6,
  }));

  const finished = games
    .filter((game) => game.status === "finished")
    .map((game) => ({
      url: `${siteUrl}/games/${game.id}`,
      lastModified: game.ended_at ? new Date(game.ended_at) : undefined,
      changeFrequency: "never" as const,
      priority: 0.5,
    }));

  return [...statics, ...catalogue, ...events, ...finished];
}

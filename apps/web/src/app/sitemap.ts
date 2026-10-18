import type { MetadataRoute } from "next";

import { listCatalogue, listGames, listTournaments } from "@/lib/api";
import { gameIsIndexable, modelIsIndexable } from "@/lib/indexing";
import { siteUrl, staticRoutes } from "@/lib/site";

/* Built static by default, which would freeze the game list at deploy time. Hourly is the right
   cadence for a sitemap — crawlers do not need a game the second it finishes. */
export const revalidate = 3600;

/**
 * An hour, asked for explicitly, because a route's own `revalidate` is only ever a **ceiling**.
 *
 * Next.js takes the shortest life among a segment's own `revalidate` and every `fetch` inside it,
 * so once the reads below started carrying the site's five-minute fallback (ADR-0046) the hour
 * above quietly became five minutes: the build's route table said `5m` where it had said `1h`, and
 * nothing else in the site would ever have shown it. A sitemap being rebuilt twelve times an hour
 * is waste rather than breakage, which is exactly the kind of regression that survives.
 */
const SITEMAP_LIFE = 3600;

/**
 * Static pages, every model that has played, every tournament, and the most recent finished games
 * long enough to be worth reading — `lib/indexing` decides which, for the pages and this alike.
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
    listGames(undefined, 200, { cache: SITEMAP_LIFE }),
    listCatalogue({ cache: SITEMAP_LIFE }),
    listTournaments(100, { cache: SITEMAP_LIFE }),
  ]);

  const statics: MetadataRoute.Sitemap = staticRoutes.map((route) => ({
    url: `${siteUrl}${route.path === "/" ? "" : route.path}`,
    changeFrequency: route.changeFrequency,
    priority: route.priority,
  }));

  const catalogue: MetadataRoute.Sitemap = models.filter(modelIsIndexable).map((model) => ({
    url: `${siteUrl}/models/${model.openrouter_id}`,
    lastModified: model.last_played_at ? new Date(model.last_played_at) : undefined,
    changeFrequency: "weekly" as const,
    priority: 0.4,
  }));

  const events: MetadataRoute.Sitemap = tournaments.map((tournament) => ({
    url: `${siteUrl}/tournaments/${tournament.slug}`,
    /* The latest game, not the tournament row: a pool never ends, and its row stands still while
       the table under it changes every day. */
    lastModified: tournament.stats.last_game_at
      ? new Date(tournament.stats.last_game_at)
      : undefined,
    changeFrequency: "daily" as const,
    priority: 0.6,
  }));

  const finished = games
    .filter((game) => game.status === "finished" && gameIsIndexable(game))
    .map((game) => ({
      url: `${siteUrl}/games/${game.id}`,
      lastModified: game.ended_at ? new Date(game.ended_at) : undefined,
      changeFrequency: "never" as const,
      priority: 0.5,
    }));

  return [...statics, ...catalogue, ...events, ...finished];
}

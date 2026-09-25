/**
 * The archive's social card — for the whole archive and for any filtered view of it (UI-12).
 *
 * **A Route Handler, because the card depends on the query string.** A colocated
 * `games/opengraph-image.tsx` receives route params and never `searchParams`, so every filtered
 * link would unfurl as the same card. The page names this URL, with its own filter as the query,
 * in `generateMetadata`.
 *
 * **The card is the list.** The filter in words, then the newest games it matches, each with its
 * result — so a link to "every draw between these two" shows the draws. The board is the first
 * game in the list that has moves, never the opening position: a board beside a list of games is
 * read as one of *those* games (`og/featured.ts` has the longer argument).
 *
 * **Rendered per request.** Reading the query makes the handler dynamic, and the variants are too
 * many to prerender. What it reads is cached: `listArchive` is the page's own tagged read, so an
 * unfurl costs a render and no API round trip. Unfurlers fetch a card once per link, not per view.
 */

import { ImageResponse } from "next/og";

import { listArchive, listModels, listTournaments } from "@/lib/api";
import { apiQuery, describeArchive, paginate, parseArchive } from "@/lib/archive";
import { Board } from "@/lib/og/board";
import { featuredFen } from "@/lib/og/featured";
import { clip } from "@/lib/og/clip";
import { Card, Standings, Stats, Subtitle, Title, Wordmark } from "@/lib/og/shell";
import { CARD } from "@/lib/og/theme";
import type { GameSummary } from "@/lib/types";

/** How many games the card lists. Five fit beside the board at a legible size. */
const SHOWN = 5;

/**
 * A seat, as short as it can be said. Two display names share one row and the row is clipped at
 * thirty characters, so "Google: Gemini 2.5 Flash" lost the part that says which model it is. A
 * model's id without its vendor keeps it; a person has only their name.
 */
function seat(game: GameSummary, colour: "white" | "black"): string {
  const player = game.players.find((p) => p.colour === colour);
  if (!player) return "?";
  return player.model ? (player.model.split("/").at(-1) ?? player.model) : player.display_name;
}

function standing(game: GameSummary): string {
  if (game.status === "running") return "live";
  if (game.status === "paused" || game.status === "aborted" || game.status === "pending") {
    return game.status;
  }
  return game.result;
}

export async function GET(request: Request) {
  const params = Object.fromEntries(new URL(request.url).searchParams);
  // The card is of the list, never of one page of it.
  const filter = { ...parseArchive(params), before: undefined };

  const [rows, models, tournaments] = await Promise.all([
    listArchive(apiQuery(filter)),
    listModels(),
    listTournaments(),
  ]);
  const { headline, qualifiers } = describeArchive(filter, {
    models: Object.fromEntries(models.map((m) => [m.openrouter_id, m.display_name])),
    events: Object.fromEntries(tournaments.map((t) => [t.slug, t.name])),
  });

  const page = rows ? paginate(rows) : null;
  const games = page?.games ?? [];
  const fen = await featuredFen(games);

  return new ImageResponse(
    <Card board={<Board fen={fen} square={54} />}>
      <Wordmark section="Games" />
      <Title>{clip(headline, 40)}</Title>
      {qualifiers.length > 0 && <Subtitle>{clip(qualifiers.join(" · "), 70)}</Subtitle>}

      {games.length === 0 && (
        /* Said in words, for both an empty filter and an unreachable API: a card listing nothing
           beside a board would read as a list that failed to draw. */
        <Subtitle>{page === null ? "The archive could not be read" : "No games match"}</Subtitle>
      )}

      {games.length > 0 && (
        <Standings
          rows={games.slice(0, SHOWN).map((game, index) => ({
            place: index + 1,
            name: `${seat(game, "white")} – ${seat(game, "black")}`,
            figure: standing(game),
            muted: game.status === "aborted",
          }))}
        />
      )}

      {games.length > 0 && (
        <Stats
          items={[
            {
              /* No count query stands behind a filter, so past one page the honest figure is "at
                 least a page". Not the summary's total for the unfiltered card either: that counts
                 aborted games, which this list hides, and the card would contradict its own page. */
              value: page?.more ? `${games.length}+` : String(games.length),
              label: games.length === 1 && !page?.more ? "game" : "games",
            },
          ]}
        />
      )}
    </Card>,
    CARD,
  );
}

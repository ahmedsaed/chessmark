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
import { shortName } from "@/lib/og/names";
import { Card, Stats, Subtitle, Title, Wordmark } from "@/lib/og/shell";
import { CARD, COLOUR } from "@/lib/og/theme";
import type { GameSummary } from "@/lib/types";

/** How many games the card lists: four two-line rows fit beside the board at a legible size. */
const SHOWN = 4;

/** A seat's name, short (`og/names.ts`). A person's name is theirs and is left alone. */
function seat(game: GameSummary, colour: "white" | "black"): string {
  return shortName(game.players.find((p) => p.colour === colour)?.display_name ?? "?");
}

/**
 * The games, each on two lines — White above Black — with its result.
 *
 * Not `Standings`, which gives a row one line and thirty characters. Two model names do not fit
 * in thirty characters: on production's data every row came out as
 * `nemotron-3-ultra-550b-a55b:...`, so a card about a matchup could not say who either side was.
 * One name per line fits the longest short name in the pool with room to spare.
 */
function GameRows({ games }: { games: GameSummary[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", marginTop: 24, gap: 14 }}>
      {games.map((game, index) => {
        const newest = index === 0;
        return (
          <div key={game.id} style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                flexGrow: 1,
                flexBasis: 0,
                minWidth: 0,
                fontSize: 23,
                lineHeight: 1.25,
                color: game.status === "aborted" ? COLOUR.inkDim : COLOUR.ink,
              }}
            >
              {(["white", "black"] as const).map((colour) => (
                <div
                  key={colour}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                  }}
                >
                  {/* The page's own colour marks, so the card says which line was White. */}
                  <div
                    style={{
                      display: "flex",
                      width: 11,
                      height: 11,
                      flexShrink: 0,
                      border: `1px solid ${COLOUR.inkFaint}`,
                      background: colour === "white" ? COLOUR.ink : "transparent",
                    }}
                  />
                  {clip(seat(game, colour), 34)}
                </div>
              ))}
            </div>
            <div
              style={{
                display: "flex",
                width: 104,
                flexShrink: 0,
                justifyContent: "flex-end",
                whiteSpace: "nowrap",
                fontSize: 25,
                color: newest ? COLOUR.accent : COLOUR.inkDim,
              }}
            >
              {standing(game)}
            </div>
          </div>
        );
      })}
    </div>
  );
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
    models: Object.fromEntries(models.map((m) => [m.openrouter_id, shortName(m.display_name)])),
    events: Object.fromEntries(tournaments.map((t) => [t.slug, t.name])),
  });

  const page = rows ? paginate(rows) : null;
  const games = page?.games ?? [];
  const fen = await featuredFen(games);

  return new ImageResponse(
    <Card board={<Board fen={fen} square={54} />}>
      <Wordmark section="Games" />
      {/* Smaller when it is long: a matchup is two names and the whole point of its card. */}
      <Title size={headline.length > 30 ? 40 : 52}>{headline}</Title>
      {qualifiers.length > 0 && <Subtitle>{clip(qualifiers.join(" · "), 70)}</Subtitle>}

      {games.length === 0 && (
        /* Said in words, for both an empty filter and an unreachable API: a card listing nothing
           beside a board would read as a list that failed to draw. */
        <Subtitle>{page === null ? "The archive could not be read" : "No games match"}</Subtitle>
      )}

      {games.length > 0 && <GameRows games={games.slice(0, SHOWN)} />}

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

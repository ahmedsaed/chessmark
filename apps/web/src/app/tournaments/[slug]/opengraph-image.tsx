/**
 * A tournament's social card: the table, and what the event has cost to run.
 *
 * A pool is ranked by rating and a closed event by points (ADR-0027), so the figure beside each
 * name is whichever one actually decides that table. Printing points for a pool would put a number
 * on the card that does not explain the order it is printed in.
 */

import { ImageResponse } from "next/og";

import { getTournament } from "@/lib/api";
import { Board } from "@/lib/og/board";
import { featuredFen } from "@/lib/og/featured";
import { Card, Missing, Standings, Stats, Title, Wordmark } from "@/lib/og/shell";
import { CARD, CONTENT_TYPE, COLOUR, REVALIDATE_SECONDS } from "@/lib/og/theme";

export const alt = "A Chessmark tournament";
export const size = CARD;
export const contentType = CONTENT_TYPE;
export const revalidate = REVALIDATE_SECONDS;

export default async function Image({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const tournament = await getTournament(slug);

  if (!tournament) {
    return new ImageResponse(<Missing what="No such tournament" />, size);
  }

  /* This event's own live game, or its most recent — the schedule is already on the payload, so
     the board costs one read rather than a list. */
  const fen = await featuredFen(tournament.games);
  const ranked = tournament.standings.some((row) => row.rating !== null);
  const top = tournament.standings.slice(0, 5);
  const { stats } = tournament;

  return new ImageResponse(
    (
      <Card board={<Board fen={fen} square={54} />}>
          <Wordmark section={tournament.status} />
          <Title size={46}>{tournament.name}</Title>

          <Standings
            rows={top.map((row) => ({
              place: row.place,
              name: row.display_name,
              figure:
                ranked && row.rating !== null
                  ? `${Math.round(row.rating)}${row.rating_provisional ? "?" : ""}`
                  : ranked
                    ? "unrated"
                    : row.score.toFixed(1),
              // A model that has left the field keeps its record and gains no more games, and the
              // table says so by dimming it. The card says it the same way.
              muted: !row.in_field,
            }))}
          />

          <Stats
            items={[
              { value: String(stats.played), label: "played" },
              { value: String(stats.pairings), label: "pairings" },
              {
                value: String(stats.illegal_attempts),
                // Zero illegal attempts is the *good* outcome, and the site colours it that way.
                // A red `0` says the opposite of what the number means.
                label: "illegal",
                tone: stats.illegal_attempts > 0 ? COLOUR.bad : COLOUR.good,
              },
            ]}
          />
      </Card>
    ),
    size,
  );
}

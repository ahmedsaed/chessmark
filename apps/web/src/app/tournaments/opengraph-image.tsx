/**
 * The tournaments index card: what is running now, and what has been run.
 *
 * A running event is the thing worth linking to, so it is named on the card. "3 tournaments" is a
 * count; "Free Models · running" is a reason to click.
 */

import { ImageResponse } from "next/og";

import { listGames, listTournaments } from "@/lib/api";
import { Board } from "@/lib/og/board";
import { featuredFen } from "@/lib/og/featured";
import { sentenceCase } from "@/lib/og/clip";
import { Card, Standings, Stats, Title, Wordmark } from "@/lib/og/shell";
import { CARD, CONTENT_TYPE, COLOUR, REVALIDATE_SECONDS } from "@/lib/og/theme";

export const alt = "Chessmark tournaments";
export const size = CARD;
export const contentType = CONTENT_TYPE;
export const revalidate = REVALIDATE_SECONDS;

export default async function Image() {
  const [tournaments, fen] = await Promise.all([
    listTournaments(6),
    listGames(undefined, 30).then(featuredFen),
  ]);
  const running = tournaments.filter((event) => event.status === "running").length;

  return new ImageResponse(
    (
      <Card board={<Board fen={fen} square={54} />}>
          <Wordmark section="Tournaments" />
          <Title>Events</Title>

          <Standings
            highlightFirst={false}
            rows={tournaments.slice(0, 4).map((event, index) => ({
              place: index + 1,
              name: event.name,
              figure: sentenceCase(event.status),
              // A finished or abandoned event is still worth listing and is not the live one.
              muted: event.status !== "running",
            }))}
          />

          <Stats
            items={[
              { value: String(tournaments.length), label: "events" },
              { value: String(running), label: "running", tone: COLOUR.machine },
            ]}
          />
      </Card>
    ),
    size,
  );
}

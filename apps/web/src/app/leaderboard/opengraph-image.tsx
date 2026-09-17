/**
 * The leaderboard's social card: who is winning, and by how much.
 *
 * The ranking *is* the page, so the card is the ranking — the top five with their ratings, not a
 * logo and the word "Leaderboard". A reader scrolling a timeline should be able to learn the
 * current order without following the link, and follow it because they want the transcripts.
 *
 * The board beside it is the opening position and carries no information on purpose: it is the
 * site's face, and every Chessmark card having one is what makes them recognisable as a set.
 */

import { ImageResponse } from "next/og";

import { getLeaderboard } from "@/lib/api";
import { Board } from "@/lib/og/board";
import { START_PLACEMENT } from "@/lib/og/fen";
import { Card, Panel, Standings, Stats, Title, Wordmark } from "@/lib/og/shell";
import { CARD, CONTENT_TYPE, COLOUR, pieceFont, REVALIDATE_SECONDS } from "@/lib/og/theme";

export const alt = "The Chessmark leaderboard — Glicko-2 ratings over ranked games";
export const size = CARD;
export const contentType = CONTENT_TYPE;
export const revalidate = REVALIDATE_SECONDS;

export default async function Image() {
  const [leaderboard, fonts] = await Promise.all([getLeaderboard(), pieceFont()]);
  const top = leaderboard.rows.slice(0, 5);

  return new ImageResponse(
    (
      <Card>
        <Board fen={START_PLACEMENT} square={54} />

        <Panel>
          <Wordmark section="Leaderboard" />

          {top.length === 0 ? (
            <>
              {/* An empty board is the honest state before any ranked game has been played, and
                  `getLeaderboard` returns it rather than throwing. The card says so in words
                  instead of drawing five blank rows. */}
              <Title>No ranked games yet</Title>
            </>
          ) : (
            <>
              <Standings
                rows={top.map((row) => ({
                  place: leaderboard.rows.indexOf(row) + 1,
                  name: row.model_slug,
                  // The deviation is dropped here and nowhere else on the site. A card is read at
                  // a glance and at a quarter of the size; `1949?` already says "not settled".
                  figure: `${Math.round(row.rating)}${row.provisional ? "?" : ""}`,
                }))}
              />
              <Stats
                items={[
                  { value: String(leaderboard.games_counted), label: "games counted" },
                  { value: String(leaderboard.rows.length), label: "contestants" },
                  {
                    value: String(leaderboard.excluded.length),
                    label: "excluded",
                    tone: COLOUR.inkDim,
                  },
                ]}
              />
            </>
          )}
        </Panel>
      </Card>
    ),
    { ...size, fonts },
  );
}

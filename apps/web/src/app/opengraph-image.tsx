/**
 * The site's own social card.
 *
 * Games have had one since Phase 8; the root URL had none, so sharing Chessmark itself produced a
 * blank rectangle. Same furniture as every other card — the opening position beside the pitch — so
 * a link to the site and a link to one of its games look like the same place.
 */

import { ImageResponse } from "next/og";

import { listGames } from "@/lib/api";
import { Board } from "@/lib/og/board";
import { featuredFen } from "@/lib/og/featured";
import { Card, Stats, Subtitle, Title, Wordmark } from "@/lib/og/shell";
import { CARD, CONTENT_TYPE, REVALIDATE_SECONDS } from "@/lib/og/theme";
import { siteTagline } from "@/lib/site";

export const alt = "Chessmark — language models play chess";
export const size = CARD;
export const contentType = CONTENT_TYPE;
export const revalidate = REVALIDATE_SECONDS;

export default async function Image() {
  // The live game if there is one, exactly as the landing page chooses its hero.
  const fen = await featuredFen(await listGames(undefined, 30));

  return new ImageResponse(
    (
      <Card board={<Board fen={fen} square={54} />}>
        <Wordmark />
        <Title size={44}>{siteTagline}</Title>
        <Subtitle>
          Agents move through tools. Every request, reasoning trace and taunt is recorded.
        </Subtitle>

        <Stats
          items={[
            { value: "Glicko-2", label: "ratings" },
            { value: "Verbatim", label: "transcripts" },
            { value: "Ply by ply", label: "replay" },
          ]}
        />
      </Card>
    ),
    size,
  );
}

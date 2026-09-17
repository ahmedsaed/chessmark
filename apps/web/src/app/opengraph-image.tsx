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
import { CARD, CONTENT_TYPE , REVALIDATE_SECONDS } from "@/lib/og/theme";
import { siteTagline } from "@/lib/site";

export const alt = "Chessmark — language models play chess";
export const size = CARD;
export const contentType = CONTENT_TYPE;
/* **The literal, not `REVALIDATE_SECONDS`.** Next requires a segment config export to be
   statically analysable and fails the production build with "Invalid segment configuration export
   detected" if it is an imported constant — a `next build`-only error, which `make check` did not
   run and so did not catch. Five minutes; `og/theme.ts` holds the reasoning. */
export const revalidate = 300;

export default async function Image() {
  // The live game if there is one, exactly as the landing page chooses its hero.
  const fen = await featuredFen(await listGames(undefined, 30, { cache: REVALIDATE_SECONDS }));

  return new ImageResponse(
    (
      <Card board={<Board fen={fen} square={54} />}>
        <Wordmark />
        {/* Three lines, because the tagline is a sentence and a sentence that stops halfway is worse
            than no tagline at all. It read "Language models play chess. Everything is" on the most
            shared URL the site has. */}
        <Title size={44} lines={3}>
          {siteTagline}
        </Title>
        {/* Not "…is recorded" — the tagline above already ends on that word, and the two lines
            landed one under the other. */}
        <Subtitle>
          Agents move through tools. Every request, reasoning trace and taunt is replayable.
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

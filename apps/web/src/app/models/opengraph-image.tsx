/**
 * The catalogue's social card: how many models are playable, and how many cost nothing.
 *
 * The free count is on the card because it is the number that decides whether a reader can try
 * this themselves, which is the only question a catalogue link raises.
 */

import { ImageResponse } from "next/og";

import { listGames, listModels } from "@/lib/api";
import { Board } from "@/lib/og/board";
import { featuredFen } from "@/lib/og/featured";
import { Card, Stats, Subtitle, Title, Wordmark } from "@/lib/og/shell";
import { CARD, CONTENT_TYPE, COLOUR, REVALIDATE_SECONDS } from "@/lib/og/theme";

export const alt = "Every model Chessmark can field";
export const size = CARD;
export const contentType = CONTENT_TYPE;
export const revalidate = REVALIDATE_SECONDS;

export default async function Image() {
  const [models, fen] = await Promise.all([
    listModels(),
    listGames(undefined, 30).then(featuredFen),
  ]);
  const free = models.filter((model) => model.is_free).length;
  const reasoning = models.filter((model) => model.supports_reasoning).length;

  return new ImageResponse(
    (
      <Card board={<Board fen={fen} square={54} />}>
          <Wordmark section="Models" />
          <Title>The catalogue</Title>
          <Subtitle>Every model that can be fielded, and what it costs to play.</Subtitle>

          <Stats
            items={[
              { value: String(models.length), label: "playable" },
              { value: String(free), label: "free", tone: COLOUR.good },
              { value: String(reasoning), label: "reasoning" },
            ]}
          />
      </Card>
    ),
    size,
  );
}

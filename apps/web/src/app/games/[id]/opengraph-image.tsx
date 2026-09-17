/**
 * A game's social card: the final position, and who lost it.
 *
 * A shared Chessmark link should say something before anyone clicks. The board is the whole pitch —
 * two language models played this, and here is how it ended — so the card renders the actual
 * position rather than a logo.
 *
 * This drew its own board, its own palette and its own layout until `lib/og` existed. Its colours
 * had drifted: the squares were `#b3a795`/`#5f5445` against the design system's
 * `#9c8869`/`#4b3f2f`, so a link to a game and a link to the site produced visibly different
 * boards. Sharing the module fixed that by construction.
 */

import { ImageResponse } from "next/og";

import { getGame } from "@/lib/api";
import { Board } from "@/lib/og/board";
import { sentenceCase } from "@/lib/og/clip";
import { Card, Missing, Stats, Title, Wordmark } from "@/lib/og/shell";
import { CARD, CONTENT_TYPE, COLOUR, REVALIDATE_SECONDS } from "@/lib/og/theme";

export const alt = "A Chessmark game";
export const size = CARD;
export const contentType = CONTENT_TYPE;
export const revalidate = REVALIDATE_SECONDS;

export default async function Image({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const game = await getGame(id);

  if (!game) return new ImageResponse(<Missing what="No such game" />, size);

  const white = game.players.find((player) => player.colour === "white");
  const black = game.players.find((player) => player.colour === "black");
  const illegal = game.players.reduce((total, player) => total + player.illegal_attempts, 0);
  const live = game.status === "running";

  return new ImageResponse(
    (
      <Card board={<Board fen={game.current_fen} square={54} />}>
        <Wordmark section={live ? "Live" : "Game"} />

        <Title size={34}>
          {white?.display_name ?? "White"} vs {black?.display_name ?? "Black"}
        </Title>

        {/* The verdict gets its own row rather than a slot in `Stats`, because `Stats` uppercases
            its labels and a termination is a word, not a column heading — `Checkmate`, not
            `CHECKMATE` beside `PLIES`. It is also the thing a reader looks for first. */}
        <div style={{ display: "flex", marginTop: 26, alignItems: "center", gap: 16 }}>
          <div
            style={{
              display: "flex",
              fontSize: 38,
              color: live ? COLOUR.machine : COLOUR.accent,
              border: `2px solid ${live ? COLOUR.machine : COLOUR.accent}`,
              padding: "2px 16px",
            }}
          >
            {live ? "Live" : game.result}
          </div>
          {!live && game.termination && (
            <div style={{ display: "flex", fontSize: 24, color: COLOUR.inkDim }}>
              {sentenceCase(game.termination.replace(/_/g, " "))}
            </div>
          )}
        </div>

        <Stats
          items={[
            { value: String(game.ply_count), label: "plies" },
            {
              value: String(illegal),
              // Zero is the good end of this scale, and the site colours it that way.
              label: "illegal",
              tone: illegal > 0 ? COLOUR.bad : COLOUR.good,
            },
          ]}
        />
      </Card>
    ),
    size,
  );
}

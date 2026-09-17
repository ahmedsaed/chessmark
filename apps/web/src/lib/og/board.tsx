/**
 * A chess board, drawn for a social card.
 *
 * **Eight explicit rows, never a wrapping container.** Satori's `flex-wrap` does not lay a
 * fixed-size grid out reliably — the first version of the game card tried it and got ragged
 * columns that overflowed the image. Rows of rows is unambiguous in a way wrapping is not, and
 * this comment is here because the wrapping version looks correct in the source.
 *
 * **Both colours are drawn with the filled glyphs**, separated by `color`. The outline set
 * (`U+2654`–`U+2659`) is what a "white" piece nominally is, and a white outline on a light square
 * is close to invisible; the filled shapes read on every square and match a physical board. See
 * `assets/README.md`.
 */

import { ranks } from "@/lib/og/fen";
import { COLOUR } from "@/lib/og/theme";

export function Board({ fen, square = 62 }: { fen: string; square?: number }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        border: `2px solid ${COLOUR.inkFaint}`,
      }}
    >
      {ranks(fen).map((rank, rankIndex) => (
        <div key={rankIndex} style={{ display: "flex" }}>
          {rank.map((piece, fileIndex) => (
            <div
              key={fileIndex}
              style={{
                display: "flex",
                width: square,
                height: square,
                // Satori will otherwise shrink a square to fit its row, which is how the board
                // came out rectangular the first time.
                flexShrink: 0,
                alignItems: "center",
                justifyContent: "center",
                background:
                  (rankIndex + fileIndex) % 2 === 0 ? COLOUR.squareLight : COLOUR.squareDark,
                fontFamily: "ChessPieces",
                fontSize: Math.round(square * 0.74),
                color: piece?.white ? COLOUR.pieceWhite : COLOUR.pieceBlack,
              }}
            >
              {piece?.glyph ?? ""}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

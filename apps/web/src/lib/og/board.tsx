/**
 * A chess board, drawn for a social card.
 *
 * **The same pieces the site itself draws** — the Cburnett SVGs `react-chessboard` renders, which
 * is what `PlayerBar` uses for a capture. A card and the page it links to now show the same shapes,
 * and the card no longer needs a font to draw a knight.
 *
 * They come from `og/pieces.ts` rather than from the package, because importing `react-chessboard`
 * here fails: it has one entry point, it carries its `ChessboardProvider`, and `createContext` does
 * not exist in a Server Component. That file says the rest.
 *
 * That replaced a vendored six-glyph subset of DejaVu Sans. Glyphs were the safe choice while it
 * was unclear whether Satori would rasterise an inline `<svg>` at this size, and they cost what a
 * text glyph always costs: one weight, one silhouette, no outline separating a black piece from a
 * dark square. The SVGs carry their own `stroke`, so both colours read on both squares.
 *
 * **Eight explicit rows, never a wrapping container.** Satori's `flex-wrap` does not lay a
 * fixed-size grid out reliably — the first version of the game card tried it and got ragged columns
 * that overflowed the image. Rows of rows is unambiguous in a way wrapping is not, and this comment
 * is here because the wrapping version looks correct in the source.
 */

import { pieceKey } from "@/lib/captures";
import { ranks } from "@/lib/og/fen";
import { pieceImage } from "@/lib/og/pieces";
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
          {rank.map((piece, fileIndex) => {
            const image = piece
              ? pieceImage(pieceKey(piece.piece, piece.white ? "white" : "black"))
              : null;

            return (
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
                }}
              >
                {image && (
                  /* Sized explicitly: the SVG declares `width: 100%`, which needs something
                     definite to be 100% *of*, and Satori will not infer it from the square.
                     Slightly inset, as the pieces sit on the board itself. */
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={image}
                    alt=""
                    width={Math.round(square * 0.86)}
                    height={Math.round(square * 0.86)}
                  />
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

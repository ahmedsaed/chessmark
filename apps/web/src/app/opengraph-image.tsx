/**
 * The site's social card.
 *
 * Games have had one since Phase 8; the root URL had none, so sharing the site itself produced a
 * blank rectangle. Same visual language as the game card — a board, drawn from the design tokens'
 * values — so a link to Chessmark and a link to a Chessmark game look like the same place.
 *
 * Satori lays the board out as eight explicit rows. `flexWrap` on a fixed grid does not work
 * here: it produced ragged, overflowing columns when the game card first tried it.
 */

import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { siteTagline } from "@/lib/site";

export const alt = "Chessmark — language models play chess";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const GLYPH: Record<string, string> = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };

const INK = "#efe8da";
const DIM = "#a99e8b";
const FAINT = "#756b5b";
const GROUND = "#16130e";
const ACCENT = "#dca84b";
const LIGHT_SQUARE = "#9c8869";
const DARK_SQUARE = "#4b3f2f";
const PIECE_WHITE = "#f5efe2";
const PIECE_BLACK = "#14110c";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR";

/** Rank strings to an 8×8 grid. One row per rank, never a wrapped flex container. */
function rows(placement: string): ({ glyph: string; white: boolean } | null)[][] {
  return placement.split("/").map((rank) => {
    const cells: ({ glyph: string; white: boolean } | null)[] = [];
    for (const char of rank) {
      if (char >= "1" && char <= "8") {
        for (let i = 0; i < Number(char); i += 1) cells.push(null);
      } else {
        cells.push({ glyph: GLYPH[char.toLowerCase()] ?? "", white: char === char.toUpperCase() });
      }
    }
    return cells;
  });
}

export default async function Image() {
  const pieces = await readFile(join(process.cwd(), "assets/chess-pieces.ttf"));
  const fonts = [
    { name: "ChessPieces", data: pieces, style: "normal" as const, weight: 400 as const },
  ];

  const board = rows(START);
  const cell = 62;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          gap: 64,
          padding: "0 72px",
          background: GROUND,
          color: INK,
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", border: `2px solid ${FAINT}` }}>
          {board.map((rank, r) => (
            <div key={r} style={{ display: "flex" }}>
              {rank.map((square, f) => (
                <div
                  key={f}
                  style={{
                    width: cell,
                    height: cell,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: (r + f) % 2 === 0 ? LIGHT_SQUARE : DARK_SQUARE,
                    fontFamily: "ChessPieces",
                    fontSize: 46,
                    color: square?.white ? PIECE_WHITE : PIECE_BLACK,
                  }}
                >
                  {square?.glyph ?? ""}
                </div>
              ))}
            </div>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div style={{ display: "flex", fontSize: 22, letterSpacing: 8, color: ACCENT }}>
            CHESSMARK
          </div>
          <div style={{ display: "flex", marginTop: 22, fontSize: 52, lineHeight: 1.15 }}>
            {siteTagline}
          </div>
          <div style={{ display: "flex", marginTop: 26, fontSize: 24, color: DIM, lineHeight: 1.4 }}>
            Agents move through tools. Every request, reasoning trace, and taunt is stored and
            replayable.
          </div>
          <div style={{ display: "flex", marginTop: 34, gap: 18, fontSize: 20, color: FAINT }}>
            <div style={{ display: "flex" }}>Glicko-2 ratings</div>
            <div style={{ display: "flex" }}>·</div>
            <div style={{ display: "flex" }}>Full transcripts</div>
            <div style={{ display: "flex" }}>·</div>
            <div style={{ display: "flex" }}>Ply-by-ply replay</div>
          </div>
        </div>
      </div>
    ),
    { ...size, fonts },
  );
}

/**
 * The social preview card: the final position, and who lost it.
 *
 * A shared Chessmark link should say something before anyone clicks. The board is the whole
 * pitch — two language models played this, and here is how it ended — so the card renders the
 * actual final position rather than a logo.
 *
 * Pieces are drawn as text in a six-glyph subset of DejaVu Sans (`assets/`). Satori has no access
 * to system fonts, so the face travels with the code.
 */

import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { getGame } from "@/lib/api";

export const alt = "Chessmark game";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** Filled glyphs for both colours; `color` separates them (see assets/README.md). */
const GLYPH: Record<string, string> = {
  k: "♚",
  q: "♛",
  r: "♜",
  b: "♝",
  n: "♞",
  p: "♟",
};

const INK = "#e8e2d9";
const DIM = "#8c857b";
const SURFACE = "#16130f";
const PANEL = "#1e1a15";
const LINE = "#332c24";
const ACCENT = "#d99a2b";
const LIGHT_SQUARE = "#b3a795";
const DARK_SQUARE = "#5f5445";

/** FEN's placement field to 64 squares, rank 8 first — the order they are drawn in. */
function squares(fen: string): (string | null)[] {
  const placement = fen.split(" ")[0] ?? "";
  const cells: (string | null)[] = [];

  for (const rank of placement.split("/")) {
    for (const character of rank) {
      if (character >= "1" && character <= "8") {
        cells.push(...Array<null>(Number(character)).fill(null));
      } else {
        cells.push(character);
      }
    }
  }

  // A malformed FEN must not throw inside image generation — pad or trim to a full board.
  return cells.length >= 64 ? cells.slice(0, 64) : [...cells, ...Array<null>(64 - cells.length).fill(null)];
}

export default async function Image({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [game, pieces] = await Promise.all([
    getGame(id),
    readFile(join(process.cwd(), "assets/chess-pieces.ttf")),
  ]);

  const fonts = [
    { name: "ChessPieces", data: pieces, style: "normal" as const, weight: 400 as const },
  ];

  if (!game) {
    return new ImageResponse(
      (
        <div
          style={{
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: SURFACE,
            color: DIM,
            fontSize: 42,
          }}
        >
          Game not found
        </div>
      ),
      { ...size, fonts },
    );
  }

  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");
  const cells = squares(game.current_fen);
  const illegal = game.players.reduce((total, p) => total + p.illegal_attempts, 0);
  const live = game.status === "running";

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          background: SURFACE,
          padding: 48,
          gap: 48,
          alignItems: "center",
          color: INK,
        }}
      >
        <Board cells={cells} />

        <div style={{ display: "flex", flexDirection: "column", flex: 1, gap: 20 }}>
          <div style={{ display: "flex", fontSize: 20, letterSpacing: 4, color: ACCENT }}>
            CHESSMARK
          </div>

          <Seat name={white?.display_name ?? "White"} model={white?.model} swatch={INK} />
          <div style={{ display: "flex", fontSize: 22, color: DIM }}>vs</div>
          <Seat name={black?.display_name ?? "Black"} model={black?.model} swatch="#2a241d" />

          <div style={{ display: "flex", gap: 12, marginTop: 8, alignItems: "center" }}>
            <div
              style={{
                display: "flex",
                fontSize: 40,
                color: live ? "#d9552b" : ACCENT,
                border: `2px solid ${live ? "#d9552b" : ACCENT}`,
                padding: "4px 18px",
              }}
            >
              {live ? "LIVE" : game.result}
            </div>
            {!live && game.termination && (
              <div style={{ display: "flex", fontSize: 22, color: DIM }}>
                {game.termination.replace(/_/g, " ")}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 24, fontSize: 22, color: DIM }}>
            <div style={{ display: "flex" }}>{game.ply_count} plies</div>
            <div style={{ display: "flex" }}>
              {illegal} illegal
            </div>
          </div>
        </div>
      </div>
    ),
    { ...size, fonts },
  );
}

function Board({ cells }: { cells: (string | null)[] }) {
  const square = 66;

  /* Eight explicit rows rather than one wrapping container. Satori's `flex-wrap` does not lay a
     fixed-size grid out reliably — it stretched the squares into ragged columns that overflowed
     the card — and rows of rows is unambiguous in a way wrapping is not. */
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        border: `3px solid ${LINE}`,
      }}
    >
      {Array.from({ length: 8 }, (_, rank) => (
        <div key={rank} style={{ display: "flex" }}>
          {cells.slice(rank * 8, rank * 8 + 8).map((piece, file) => {
            const dark = (rank + file) % 2 === 1;
            const white = piece !== null && piece === piece.toUpperCase();

            return (
              <div
                key={file}
                style={{
                  display: "flex",
                  width: square,
                  height: square,
                  flexShrink: 0,
                  alignItems: "center",
                  justifyContent: "center",
                  background: dark ? DARK_SQUARE : LIGHT_SQUARE,
                  fontSize: 50,
                  fontFamily: "ChessPieces",
                  color: white ? "#faf8f5" : "#141009",
                }}
              >
                {piece ? (GLYPH[piece.toLowerCase()] ?? "") : ""}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function Seat({
  name,
  model,
  swatch,
}: {
  name: string;
  model?: string | null;
  swatch: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
      <div
        style={{
          display: "flex",
          width: 22,
          height: 22,
          background: swatch,
          border: `2px solid ${LINE}`,
        }}
      />
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", fontSize: 34 }}>{name}</div>
        {model && (
          <div style={{ display: "flex", fontSize: 19, color: DIM, background: PANEL, padding: "2px 8px" }}>
            {model}
          </div>
        )}
      </div>
    </div>
  );
}

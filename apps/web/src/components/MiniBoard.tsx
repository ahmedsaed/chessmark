/**
 * A small, static board rendered straight from a FEN.
 *
 * Deliberately not `Board`: that pulls in react-chessboard and has to be a Client Component, and
 * three of them under the hero would ship a drag-and-drop engine to draw three thumbnails. This
 * is a CSS grid and twelve glyphs, server-rendered, no JavaScript.
 *
 * Both colours use the **filled** glyph set and are separated by colour rather than by glyph.
 * The outline set (♔♕♖) renders inconsistently across platforms — some fall back to an emoji
 * face — while the filled set is reliable and takes a `color` cleanly. The OpenGraph card uses
 * the same trick for the same reason.
 */

const GLYPH: Record<string, string> = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };

interface Square {
  glyph: string;
  white: boolean;
}

/** The placement field of a FEN to 64 squares, rank 8 first. */
function squares(fen: string): (Square | null)[] {
  const placement = fen.split(" ")[0] ?? "";
  const cells: (Square | null)[] = [];

  for (const rank of placement.split("/")) {
    for (const char of rank) {
      if (char >= "1" && char <= "8") {
        for (let i = 0; i < Number(char); i += 1) cells.push(null);
      } else {
        cells.push({
          glyph: GLYPH[char.toLowerCase()] ?? "",
          white: char === char.toUpperCase(),
        });
      }
    }
  }

  return cells;
}

export function MiniBoard({ fen, label }: { fen: string; label?: string }) {
  const cells = squares(fen);

  return (
    <div
      role="img"
      aria-label={label ?? "final position"}
      className="grid aspect-square w-full grid-cols-8 border border-line-soft"
    >
      {cells.map((square, index) => {
        const rank = Math.floor(index / 8);
        const file = index % 8;
        const light = (rank + file) % 2 === 0;

        return (
          <span
            key={index}
            aria-hidden
            className={`flex items-center justify-center leading-none ${
              light ? "bg-sq-light" : "bg-sq-dark"
            } ${square?.white ? "text-piece-white" : "text-piece-black"}`}
            style={{ fontSize: "min(2.6vw, 15px)" }}
          >
            {square?.glyph ?? ""}
          </span>
        );
      })}
    </div>
  );
}

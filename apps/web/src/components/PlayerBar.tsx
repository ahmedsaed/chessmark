"use client";

/**
 * One player's nameplate, above or below the board.
 *
 * The two names used to sit together in a single line above the board — "white — black" — which
 * is a caption, not a board. Every chess interface puts each player on their own side because
 * that is where their pieces are, and it is the only arrangement in which "who is up a rook"
 * can be read without being told which name is which.
 *
 * The captured pieces are drawn with `react-chessboard`'s own SVGs rather than ♟♜♞: those resolve
 * to whichever system font wins and render as emoji on some platforms, which is the mistake the
 * replay thumbnails made and had to undo (Phase 19).
 */

import { defaultPieces } from "react-chessboard";

import { pieceKey } from "@/lib/captures";
import type { Player } from "@/lib/types";

export function PlayerBar({
  player,
  taken,
  advantage,
  active,
  toMoveLabel,
}: {
  player: Player | undefined;
  /** What this player has captured, as lowercase piece letters, heaviest first. */
  taken: string[];
  /** Material lead in pawns. Shown only by the player who holds it. */
  advantage: number;
  active: boolean;
  /** Rendered at the far end — the result, or whose move it is. */
  toMoveLabel?: React.ReactNode;
}) {
  if (!player) return null;

  // The captured pieces belong to the opponent, so they are drawn in the opponent's colour.
  const capturedColour = player.colour === "white" ? "black" : "white";

  return (
    <div className="flex flex-none items-center gap-2 font-mono text-[11px]">
      <i
        aria-hidden
        className={`block h-2.5 w-2.5 flex-none border border-line ${
          player.colour === "white" ? "bg-piece-white" : "bg-piece-black"
        }`}
      />
      <span className={`truncate uppercase tracking-[0.08em] ${active ? "text-ink" : "text-ink-faint"}`}>
        {player.display_name}
      </span>

      {taken.length > 0 && (
        <span
          className="flex min-w-0 flex-wrap items-center"
          aria-label={`captured: ${taken.length} piece${taken.length === 1 ? "" : "s"}`}
        >
          {taken.map((piece, index) => (
            <Captured key={`${piece}-${index}`} piece={piece} colour={capturedColour} />
          ))}
        </span>
      )}

      {advantage > 0 && (
        <span className="tabular flex-none text-[10px] text-good" title="material advantage">
          +{advantage}
        </span>
      )}

      {toMoveLabel && <span className="ml-auto flex-none text-accent">{toMoveLabel}</span>}
    </div>
  );
}

/**
 * One captured piece, at text size.
 *
 * They overlap slightly: eight captured pawns in a row is wider than most nameplates, and a
 * chess interface shows them as a huddle rather than a line for exactly that reason.
 *
 * **Drawn as a silhouette in one muted tone, not in the piece's own colour.** A black piece is
 * `#14110c` against a `#16130e` page — the same colour to the eye. It reads on the board only
 * because a square is far lighter than the page behind it, and here there is no square. Which
 * side took the piece is already said by the nameplate it sits on, so the colour was carrying no
 * information the reader did not already have; the shape is the part that matters.
 */
function Captured({ piece, colour }: { piece: string; colour: "white" | "black" }) {
  const Piece = defaultPieces[pieceKey(piece, colour)];
  if (!Piece) return null;

  return (
    <span aria-hidden className="-ml-0.5 block h-[15px] w-[15px] first:ml-0 opacity-80">
      <Piece fill="var(--color-ink-dim)" />
    </span>
  );
}

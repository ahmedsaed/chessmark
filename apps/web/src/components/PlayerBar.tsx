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
    /* **Two lines on a phone, one from `sm` up.** The captures shared the name's line, and a name
       is longer than a phone: `Nex AGI: Nex-N2.5-Pro (free)` wanted 209px, got 157px, and lost a
       quarter of itself to a huddle of ten pieces. The huddle then wrapped *inside* the row, so the
       two nameplates flanking the board were 30px and 17px tall — a difference a reader can see and
       cannot account for.
       Done with `order` and a zero-height `basis-full` break rather than a nested row, because the
       desktop order is name, captures, advantage, label and a wrapper around the first and last of
       those cannot produce it. The break is the only thing that moves. */
    <div className="flex flex-none flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-data sm:flex-nowrap">
      <i
        aria-hidden
        className={`order-1 block h-2.5 w-2.5 flex-none border border-line ${
          player.colour === "white" ? "bg-piece-white" : "bg-piece-black"
        }`}
      />
      <span
        /* **`flex-1` on a phone, content-width from `sm` up.** Growing is what puts the name on a
           line of its own below `sm`, which is what pushes the captures to the row beneath. On a
           wide rail it does the opposite of what it looks like: the *box* fills the row, so the
           huddle sits against its right edge while the text ends far to the left — 284px of name
           in a 522px box left a 246px hole between the two. `flex-initial` still shrinks, so a
           long name truncates exactly as before; it just stops reserving room it is not using.
           The trailing space goes to the to-move label, which already claims it with `ml-auto`. */
        className={`order-2 min-w-0 flex-1 truncate uppercase tracking-[0.08em] sm:flex-initial ${
          active ? "text-ink" : "text-ink-faint"
        }`}
      >
        {player.display_name}
      </span>

      {advantage > 0 && (
        <span
          className="tabular order-3 flex-none text-meta text-good sm:order-4"
          title="material advantage"
        >
          +{advantage}
        </span>
      )}

      {toMoveLabel && (
        <span className="order-4 ml-auto flex-none text-accent sm:order-5">{toMoveLabel}</span>
      )}

      {taken.length > 0 && (
        <>
          {/* The line break itself. Zero height so it costs nothing, and gone at `sm` where the
              row has the width to hold everything. */}
          <span aria-hidden className="order-5 h-0 basis-full sm:hidden" />
          <span
            /* **The name is the one that yields, not this.** Making the name rigid at `sm` starved
               the huddle to 33px and stacked ten pieces into five rows in a 323px rail. The name
               truncates and the pieces keep their width — which is the trade the desktop rail was
               already making, and the only thing this change was ever meant to alter is where the
               huddle sits on a phone. */
            className="order-6 flex min-w-0 flex-wrap items-center sm:order-3 sm:flex-none"
            /* `role="img"`: a label on a bare `<span>` is prohibited and dropped, so "captured: 3
               pieces" was never read. The pieces inside are decorative; this is one picture. */
            role="img"
            aria-label={`captured: ${taken.length} piece${taken.length === 1 ? "" : "s"}`}
          >
            {taken.map((piece, index) => (
              <Captured key={`${piece}-${index}`} piece={piece} colour={capturedColour} />
            ))}
          </span>
        </>
      )}
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

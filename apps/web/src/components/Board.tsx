"use client";

/**
 * The board.
 *
 * Colours come from the design tokens rather than the library's defaults — `--color-sq-light`
 * and `--color-sq-dark` are deliberately separate tokens so board theme can become a user
 * preference later without touching the rest of the design (ADR-0013).
 *
 * Used at two very different sizes: the game page's main board, and the ~120px replay thumbnails
 * on the landing page. Those started out as a hand-rolled grid of Unicode glyphs, which was cheap
 * and rendered unreliably — ♟♜♞ depend on whichever system font wins, and some platforms give
 * them emoji presentation. The position data was correct throughout; the drawing was not. One
 * renderer for both sizes means the thumbnails cannot drift from the real board.
 */

import { Chessboard } from "react-chessboard";
import { useMemo } from "react";

interface Props {
  fen: string;
  /** Squares of the last move, highlighted so the eye can find it without replaying. */
  lastMove?: { from: string; to: string } | null;
  orientation?: "white" | "black";
  /** Off for thumbnails, where rank and file labels are unreadable clutter. */
  showNotation?: boolean;
  /** Kept below the caller's tick interval, or moves queue up behind the animation. */
  animationMs?: number;
}

export function Board({
  fen,
  lastMove,
  orientation = "white",
  showNotation = true,
  animationMs = 250,
}: Props) {
  const squareStyles = useMemo(() => {
    if (!lastMove) return {};
    const highlight = { backgroundColor: "color-mix(in srgb, var(--color-sq-mark) 45%, transparent)" };
    return { [lastMove.from]: highlight, [lastMove.to]: highlight };
  }, [lastMove]);

  return (
    <div className="w-full [&_*]:!font-sans">
      <Chessboard
        options={{
          position: fen,
          boardOrientation: orientation,
          allowDragging: false,
          showNotation,
          animationDurationInMs: animationMs,
          squareStyles,
          darkSquareStyle: { backgroundColor: "var(--color-sq-dark)" },
          lightSquareStyle: { backgroundColor: "var(--color-sq-light)" },
          darkSquareNotationStyle: { color: "var(--color-sq-light)" },
          lightSquareNotationStyle: { color: "var(--color-sq-dark)" },
          boardStyle: { borderRadius: "2px", overflow: "hidden" },
        }}
      />
    </div>
  );
}

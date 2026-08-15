"use client";

/**
 * The board.
 *
 * Colours come from the design tokens rather than the library's defaults — `--color-sq-light`
 * and `--color-sq-dark` are deliberately separate tokens so board theme can become a user
 * preference later without touching the rest of the design (ADR-0013).
 */

import { Chessboard } from "react-chessboard";
import { useMemo } from "react";

interface Props {
  fen: string;
  /** Squares of the last move, highlighted so the eye can find it without replaying. */
  lastMove?: { from: string; to: string } | null;
  orientation?: "white" | "black";
}

export function Board({ fen, lastMove, orientation = "white" }: Props) {
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
          showNotation: true,
          animationDurationInMs: 250,
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

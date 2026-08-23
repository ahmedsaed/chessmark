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
 *
 * Every instance gets its own `id`. The library measures a square with a bare
 * `document.querySelector('#{id}-square-{square}')` and defaults `id` to the constant
 * `"chessboard"`, so several boards on one page all measure the first one in the document. With
 * the 440px hero above the 120px thumbnails, the thumbnails animated using 55px squares and flung
 * their pieces most of the way across the board before snapping into place.
 */

import { Chessboard } from "react-chessboard";
import { useId, useMemo } from "react";

import { boardDomId } from "@/lib/animation";

interface Props {
  fen: string;
  /** Squares of the last move, highlighted so the eye can find it without replaying. */
  lastMove?: { from: string; to: string } | null;
  orientation?: "white" | "black";
  /** Off for thumbnails, where rank and file labels are unreadable clutter. */
  showNotation?: boolean;
  /** Kept below the caller's tick interval, or moves queue up behind the animation. */
  animationMs?: number;
  /**
   * Return `true` to accept the drop. The board is optimistic only as far as this says so — the
   * server still validates, and a move it refuses is reverted by the next position it sends.
   */
  onDrop?: (from: string, to: string, piece: string) => boolean;
  /** Squares to mark, e.g. a king in check. */
  markedSquares?: Record<string, React.CSSProperties>;
}

export function Board({
  fen,
  lastMove,
  orientation = "white",
  showNotation = true,
  animationMs = 250,
  onDrop,
  markedSquares,
}: Props) {
  const id = boardDomId(useId());

  const squareStyles = useMemo(() => {
    const highlight = {
      backgroundColor: "color-mix(in srgb, var(--color-sq-mark) 45%, transparent)",
    };
    return {
      ...(lastMove ? { [lastMove.from]: highlight, [lastMove.to]: highlight } : {}),
      ...markedSquares,
    };
  }, [lastMove, markedSquares]);

  return (
    <div className="w-full [&_*]:!font-sans">
      <Chessboard
        options={{
          id,
          position: fen,
          boardOrientation: orientation,
          allowDragging: Boolean(onDrop),
          onPieceDrop: onDrop
            ? ({ sourceSquare, targetSquare, piece }) =>
                targetSquare
                  ? onDrop(sourceSquare, targetSquare, String(piece?.pieceType ?? ""))
                  : false
            : undefined,
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

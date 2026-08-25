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
import { useCallback, useId, useMemo, useState } from "react";

import { boardDomId } from "@/lib/animation";
import type { LegalTarget } from "@/lib/board";

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
  /**
   * Where the piece on a square may legally go. Supplied by the caller rather than derived here,
   * because the caller already holds the position and the board deliberately knows no chess.
   *
   * Its presence is what turns on click-to-move and the target dots. Absent — thumbnails, a
   * spectator's board — the board behaves exactly as it did before.
   */
  targetsFor?: (square: string) => LegalTarget[];
}

export function Board({
  fen,
  lastMove,
  orientation = "white",
  showNotation = true,
  animationMs = 250,
  onDrop,
  markedSquares,
  targetsFor,
}: Props) {
  const id = boardDomId(useId());

  /** The square a click selected, if any. Dragging never sets it — the drag *is* the gesture. */
  const [selected, setSelected] = useState<string | null>(null);

  const interactive = Boolean(onDrop && targetsFor);
  const targets = useMemo(
    () => (selected && targetsFor ? targetsFor(selected) : []),
    [selected, targetsFor],
  );

  /**
   * Both gestures end here, so there is one definition of "a move was attempted" and the two
   * cannot diverge. Clearing the selection unconditionally matters: a refused move must not leave
   * a piece looking selected when the board has moved on.
   */
  const attempt = useCallback(
    (from: string, to: string, piece: string): boolean => {
      setSelected(null);
      return onDrop ? onDrop(from, to, piece) : false;
    },
    [onDrop],
  );

  const handleSquareClick = useCallback(
    ({ square, piece }: { square: string; piece: { pieceType?: unknown } | null }) => {
      if (!interactive || !targetsFor) return;

      // A second click on the selected piece puts it down again.
      if (selected === square) {
        setSelected(null);
        return;
      }

      if (selected && targets.some((target) => target.to === square)) {
        attempt(selected, square, "");
        return;
      }

      /* Selecting is gated on the piece having somewhere to go, which quietly does the access
         control: `chess.js` generates moves only for the side to move, so an opponent's piece —
         or any piece when it is not your turn — simply yields nothing and stays unselected. */
      setSelected(piece && targetsFor(square).length > 0 ? square : null);
    },
    [interactive, targetsFor, selected, targets, attempt],
  );

  const squareStyles = useMemo(() => {
    const highlight = {
      backgroundColor: "color-mix(in srgb, var(--color-sq-mark) 45%, transparent)",
    };
    const selectedStyle = {
      backgroundColor: "color-mix(in srgb, var(--color-accent) 40%, transparent)",
    };

    /* A dot for an empty square, a ring for a capture. Drawn with a radial gradient rather than
       child elements because the library owns what is inside a square — a dot as a child would be
       painted under the piece it is meant to sit beside. */
    const dot = {
      backgroundImage:
        "radial-gradient(circle, color-mix(in srgb, var(--color-accent) 55%, transparent) 16%, transparent 18%)",
    };
    const ring = {
      backgroundImage:
        "radial-gradient(circle, transparent 62%, color-mix(in srgb, var(--color-accent) 55%, transparent) 64%)",
    };

    return {
      ...(lastMove ? { [lastMove.from]: highlight, [lastMove.to]: highlight } : {}),
      ...Object.fromEntries(targets.map((t) => [t.to, t.capture ? ring : dot])),
      ...(selected ? { [selected]: selectedStyle } : {}),
      ...markedSquares,
    };
  }, [lastMove, markedSquares, selected, targets]);

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
                  ? attempt(sourceSquare, targetSquare, String(piece?.pieceType ?? ""))
                  : false
            : undefined,
          /* Picking a piece up shows the same dots a click does — the two gestures should teach
             the same thing about the position, and a player mid-drag is exactly who wants them. */
          onPieceDrag: interactive
            ? ({ square }) => setSelected(square ?? null)
            : undefined,
          onPieceDragCancel: interactive ? () => setSelected(null) : undefined,
          onSquareClick: interactive ? handleSquareClick : undefined,
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

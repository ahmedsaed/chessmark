"use client";

/**
 * The game's cost line: recorded while it plays, billed once reconciled (ADR-0054), and a tooltip
 * that says why when the two differ.
 *
 * **Hover or focus, on the number and the ⓘ alike.** The pair is one focusable element, so a
 * keyboard reaches it with Tab and a phone opens it with a tap (a tap focuses it), and the
 * explanation is wired to it with `aria-describedby`, so a screen reader reads it with the number.
 * It was a disclosure first, which pushed the rows below it down to say one sentence.
 */

import { useId } from "react";

import { gameCost, usd } from "@/lib/credit";
import type { GameDetail } from "@/lib/types";

export function CostRow({ game }: { game: GameDetail }) {
  const shown = gameCost(game);
  const noteId = useId();

  return (
    <div className="tabular flex items-center justify-between gap-2 font-mono text-data text-ink-dim">
      <span>{game.billed_usd ? "Billed" : "Total"}</span>
      {shown.note ? (
        <span className="group relative">
          <span
            // Focusable so the note is reachable without a pointer: Tab on a keyboard, a tap on a
            // phone. Not a button — there is nothing to activate, only something to read.
            tabIndex={0}
            aria-describedby={noteId}
            className="flex cursor-help items-center gap-1.5 text-ink outline-none focus-visible:underline"
          >
            {usd(shown.usd)}
            <span
              aria-hidden
              className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-label leading-none text-ink-faint group-hover:border-accent-dim group-hover:text-ink"
            >
              i
            </span>
          </span>
          <span
            id={noteId}
            role="tooltip"
            className="invisible absolute bottom-full right-0 z-20 mb-2 w-64 border border-line bg-surface-3 p-2.5 font-sans text-xs leading-relaxed text-ink-dim opacity-0 shadow-lg transition-opacity group-has-[:focus-visible]:visible group-has-[:focus-visible]:opacity-100 group-hover:visible group-hover:opacity-100"
          >
            {shown.note}
          </span>
        </span>
      ) : (
        <span className="text-ink">{usd(shown.usd)}</span>
      )}
    </div>
  );
}

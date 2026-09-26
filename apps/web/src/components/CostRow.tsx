"use client";

/**
 * The game's cost line: recorded while it plays, billed once reconciled (ADR-0054), and an ⓘ that
 * says why when the two differ.
 *
 * **A button, not a tooltip.** A `title` needs a hover a phone does not have and never reaches a
 * keyboard; this opens on tap, click or Enter, and a screen reader hears what it controls.
 */

import { useId, useState } from "react";

import { gameCost, usd } from "@/lib/credit";
import type { GameDetail } from "@/lib/types";

export function CostRow({ game }: { game: GameDetail }) {
  const shown = gameCost(game);
  const [open, setOpen] = useState(false);
  const noteId = useId();
  return (
    <div>
      <div className="tabular flex items-center justify-between gap-2 font-mono text-data text-ink-dim">
        <span>{game.billed_usd ? "Billed" : "Total"}</span>
        <span className="flex items-center gap-1.5 text-ink">
          {usd(shown.usd)}
          {/* A button, not a tooltip: a `title` needs a hover a phone does not have and never
              reaches a keyboard. This opens on tap, click or Enter. */}
          {shown.note && (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={noteId}
              aria-label="Why the cost differs from the running total"
              onClick={() => setOpen((was) => !was)}
              className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-label leading-none text-ink-faint hover:border-accent-dim hover:text-ink"
            >
              i
            </button>
          )}
        </span>
      </div>
      {shown.note && open && (
        <p id={noteId} className="mt-1.5 text-xs leading-relaxed text-ink-dim">
          {shown.note}
        </p>
      )}
    </div>
  );
}


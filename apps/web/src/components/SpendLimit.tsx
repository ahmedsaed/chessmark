"use client";

/**
 * The player's own limit on what one game may cost (ADR-0052). Optional, and theirs.
 *
 * A game spends the credit of the person who starts it, turn by turn, so the only limit is the one
 * they choose: left empty, the game plays until it ends or their credit does. Reached, the game
 * ends as `budget_exceeded`.
 */

import { useId } from "react";

export function SpendLimit({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-meta text-ink-faint"
    >
      {/* Kept whole: at phone width it broke into "STOP / AT $" beside its own box. The note is
          what wraps, onto its own line. */}
      <span className="whitespace-nowrap uppercase tracking-[0.14em]">Stop at $</span>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        min="0.01"
        step="0.01"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="no limit"
        className="tabular w-24 border border-line bg-surface px-2 py-1 text-data text-ink placeholder:text-ink-faint focus:border-accent-dim focus:outline-none"
      />
      <span className="normal-case">optional — the most this game may cost you</span>
    </label>
  );
}

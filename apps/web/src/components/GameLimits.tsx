"use client";

/**
 * A game's two limits and the button that starts it, on one row (ADR-0052).
 *
 * **Stop at $** is the player's own limit on what the game may cost: left empty there is none, and
 * the game plays until it ends or their credit does. **Ply cap** is the length it stops at — the
 * intro promised it and the form never offered it. The button sits on the row rather than beside
 * the model pickers, where each picker's price line pushed it out of line with the selectors.
 */

import { useId } from "react";

import { DEFAULT_PLIES, MAX_PLIES, MIN_PLIES } from "@/lib/limits";

/* No spin arrows: they sat over the "no limit" placeholder, and nobody steps a dollar limit up a
   cent at a time. `textfield` for Firefox, the pseudo-elements for everything else. */
const FIELD =
  "tabular border border-line bg-surface px-2 py-1 text-data text-ink [appearance:textfield] placeholder:text-ink-faint focus:border-accent-dim focus:outline-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";
const LABEL = "flex items-center gap-2 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint";

export function GameLimits({
  limit,
  onLimitChange,
  plies,
  onPliesChange,
  options,
  children,
}: {
  limit: string;
  onLimitChange: (next: string) => void;
  plies: string;
  onPliesChange: (next: string) => void;
  /** Any further setting for this kind of game, on the same row. */
  options?: React.ReactNode;
  /** The start button, at the end of the row. */
  children: React.ReactNode;
}) {
  const limitId = useId();
  const pliesId = useId();
  return (
    /* `relative` so an option's tooltip can anchor to the whole row on a phone (`TalkToggle`). */
    <div className="relative flex flex-wrap items-center gap-x-5 gap-y-3">
      <label htmlFor={limitId} className={LABEL}>
        <span className="whitespace-nowrap">Stop at $</span>
        <input
          id={limitId}
          type="number"
          inputMode="decimal"
          min="0.01"
          step="0.01"
          value={limit}
          onChange={(event) => onLimitChange(event.target.value)}
          placeholder="no limit"
          title="The most this game may cost you. Empty for no limit."
          className={`${FIELD} w-24`}
        />
      </label>
      <label htmlFor={pliesId} className={LABEL}>
        <span className="whitespace-nowrap">Ply cap</span>
        <input
          id={pliesId}
          type="number"
          inputMode="numeric"
          min={MIN_PLIES}
          max={MAX_PLIES}
          step="1"
          value={plies}
          onChange={(event) => onPliesChange(event.target.value)}
          placeholder={String(DEFAULT_PLIES)}
          title={`The game is drawn at this many plies (${MIN_PLIES}–${MAX_PLIES}).`}
          className={`${FIELD} w-20`}
        />
      </label>
      {options}
      <div className="ml-auto">{children}</div>
    </div>
  );
}

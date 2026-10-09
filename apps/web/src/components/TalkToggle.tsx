"use client";

/**
 * The switch for talk in a game, with what it means in a tooltip — on hover, or on *keyboard*
 * focus. Plain focus kept it open after a click, because clicking a checkbox focuses it.
 *
 * Trash talk between two models is one of the things this site is for, and the form that starts
 * such a game never offered it — the API turned it on by default and nobody could say otherwise.
 * The checkbox is drawn in `em` (globals.css), so it is given its own size here: beside the row's
 * small uppercase labels it rendered at nine pixels.
 */

import { useId } from "react";

export function TalkToggle({
  checked,
  onChange,
  hint,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** What talk means in this kind of game. */
  hint: string;
}) {
  const hintId = useId();
  return (
    // **Relative only from `md` up.** On a phone the toggle sits part-way across the row, and a
    // 256px box hung from its left edge ran 68px past a 390px screen — scrolling the whole page
    // sideways even while invisible, because `invisible` still takes up layout. Below `md` the
    // tooltip anchors to the enclosing row instead (`GameLimits` is `relative` for this) and spans
    // its width, which is always inside the form.
    <span className="group md:relative">
      <label className="flex cursor-pointer items-center gap-2 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint hover:text-ink-dim">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          aria-describedby={hintId}
          className="text-base"
        />
        Talk
      </label>
      <span
        id={hintId}
        role="tooltip"
        className="invisible absolute inset-x-0 bottom-full z-20 mb-2 border md:right-auto md:w-64 border-line bg-surface-3 p-2.5 font-sans text-xs normal-case leading-relaxed tracking-normal text-ink-dim opacity-0 shadow-lg transition-opacity group-has-[:focus-visible]:visible group-has-[:focus-visible]:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {hint}
      </span>
    </span>
  );
}

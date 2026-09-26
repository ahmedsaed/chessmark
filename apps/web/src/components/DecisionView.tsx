"use client";

import { useState } from "react";

import type { DecisionBlock } from "@/lib/types";

/** How many moves are drawn before "all N" — enough to show a contest, few enough to scan. */
const SHOWN = 5;

/** The actions a decision seat can take instead of a quiet move, as the timeline says them. */
const ACTION_TEXT: Record<string, string> = {
  resign: "resigned",
  claim_draw: "claimed the draw",
  accept_draw: "accepted the draw",
};

/** The other questions it is asked, in the words a reader would use. */
const QUESTION_TEXT: Record<string, string> = {
  resign: "resign",
  offer_draw: "offer draw",
  claim_draw: "claim draw",
  accept_draw: "accept draw",
};

function percent(probability: number): string {
  if (probability > 0 && probability < 0.005) return "<1%";
  return `${Math.round(probability * 100)}%`;
}

/**
 * A decision model's turn, which is its whole answer (ADR-0049).
 *
 * **Open, not folded behind a disclosure.** A chat model's reasoning is closed by default because
 * it can run to eighteen thousand tokens; a decision is a handful of rows, and it is the only view
 * there is into what the model weighed. Hiding it would leave the turn saying nothing but its move.
 *
 * The bars are decoration and hidden from assistive tech; the percentages beside them carry the
 * same fact as text, so nothing here is conveyed by width alone.
 */
export function DecisionView({ block, edge }: { block: DecisionBlock; edge: string }) {
  const [all, setAll] = useState(false);
  const ranked = block.probabilities;
  const shown = ranked && !all ? ranked.slice(0, SHOWN) : ranked;
  const acted = ACTION_TEXT[block.action];

  const head = [
    `decided among ${block.options} move${block.options === 1 ? "" : "s"}`,
    block.durationMs !== null ? `${(block.durationMs / 1000).toFixed(1)}s` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className={`flex w-full max-w-[94%] flex-col gap-1.5 ${edge} border-machine-deep`}
      data-testid="decision"
    >
      <p className="font-mono text-meta text-ink-faint">{head}</p>

      {(acted || block.offersDraw) && (
        <p className="font-mono text-meta text-accent">
          {acted ?? "offered a draw with its move"}
        </p>
      )}

      {shown === null ? (
        /* Invariant 8: the person reading this is playing the model, and its ranking of their
           position is its plan. What it did is on the board; how it weighed it waits. */
        <p className="text-xs leading-relaxed text-ink-faint">
          How it weighed its moves is shown when the game ends.
        </p>
      ) : (
        <ol className="flex flex-col gap-0.5" aria-label="How the model weighed its moves">
          {shown.map(([san, probability]) => {
            /* Marked only when it was played. A seat that resigned or took a draw still ranked a
               move first, but drawing it as "chosen" would claim a move the board never saw. */
            const chosen = block.action === "move" && san === block.choice;
            return (
              <li
                key={san}
                className="grid grid-cols-[4.5rem_1fr_2.75rem] items-center gap-2 font-mono text-meta"
              >
                <span className={chosen ? "text-accent" : "text-ink-dim"}>
                  {san}
                  {chosen && <span className="sr-only"> (chosen)</span>}
                </span>
                <span aria-hidden className="h-1.5 bg-surface-3">
                  <span
                    className={`block h-full ${chosen ? "bg-accent" : "bg-machine-dim"}`}
                    style={{ width: `${Math.max(probability * 100, 1)}%` }}
                  />
                </span>
                <span className="tabular text-right text-ink-dim">{percent(probability)}</span>
              </li>
            );
          })}
        </ol>
      )}

      {ranked && ranked.length > SHOWN && (
        <button
          type="button"
          onClick={() => setAll((open) => !open)}
          aria-expanded={all}
          className="self-start font-mono text-meta text-ink-faint underline underline-offset-4 hover:text-ink-dim"
        >
          {all ? `top ${SHOWN} only` : `all ${ranked.length} moves`}
        </button>
      )}

      {block.answers && Object.keys(block.answers).length > 0 && (
        <p className="font-mono text-meta text-ink-faint">
          {Object.entries(block.answers)
            .map(([question, probability]) => `${QUESTION_TEXT[question] ?? question} ${percent(probability)}`)
            .join(" · ")}
        </p>
      )}
    </div>
  );
}

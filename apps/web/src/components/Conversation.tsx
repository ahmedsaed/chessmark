"use client";

/**
 * The conversation panel (ADR-0013).
 *
 * A messaging-app timeline: alignment identifies the player so no entry needs a label, and the
 * move acts as a date separator closing each turn. Finished turns fold to one line; the live turn
 * stays open, which is what keeps move 60 as readable as move 3.
 *
 * Six event types, six deliberately distinct registers. Only `say` is shaped like a message —
 * that is what lets a taunt survive being surrounded by telemetry.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { TurnView } from "@/lib/types";

type Filter = "all" | "moves" | "talk";

/**
 * Ply number to chess notation. Ply 1 is "1.", ply 2 is "1…", ply 3 is "2." — a full move is two
 * plies, and Black's half is written with an ellipsis. Printing the raw ply here would read as
 * nonsense to anyone who plays chess.
 */
function moveLabel(ply: number, san: string): string {
  const moveNumber = Math.ceil(ply / 2);
  return ply % 2 === 1 ? `${moveNumber}. ${san}` : `${moveNumber}… ${san}`;
}

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "moves", label: "Moves + talk" },
  { id: "talk", label: "Talk only" },
];

export function Conversation({
  turns,
  focusKey,
  onInspect,
  emptyMessage = "Waiting for the first turn…",
}: {
  turns: TurnView[];
  /**
   * The turn to hold open. Live play passes nothing and lets `turn.live` decide; replay passes
   * the turn that produced the current ply, so scrubbing always lands on an open turn rather than
   * on a row you have to click before it says anything.
   */
  focusKey?: string | null;
  /** Opens the raw payloads behind a turn. Absent while a game is live — the API refuses them. */
  onInspect?: (turn: TurnView) => void;
  /** Replay's ply 0 is a starting position, not a game waiting to begin. */
  emptyMessage?: string;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  const visible = useMemo(
    () => (filter === "talk" ? turns.filter((turn) => turn.said.length > 0) : turns),
    [turns, filter],
  );

  function toggle(key: string) {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <section
      aria-label="Conversation"
      className="flex min-h-0 flex-col border border-line bg-surface-2"
    >
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line bg-surface-3 px-3 py-2">
        {FILTERS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setFilter(id)}
            aria-pressed={filter === id}
            className={`font-mono text-[10px] uppercase tracking-[0.1em] border px-2 py-1 transition-colors ${
              filter === id
                ? "border-accent bg-accent text-on-accent"
                : "border-line bg-surface text-ink-faint hover:text-ink-dim"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
        {visible.length === 0 && (
          <p className="font-mono text-xs text-ink-faint">{emptyMessage}</p>
        )}

        {visible.map((turn) => (
          <Turn
            key={turn.key}
            turn={turn}
            filter={filter}
            open={turn.live || turn.key === focusKey || expanded.has(turn.key)}
            onToggle={() => toggle(turn.key)}
            onInspect={onInspect && (() => onInspect(turn))}
          />
        ))}
        <div ref={bottom} />
      </div>
    </section>
  );
}

function Turn({
  turn,
  filter,
  open,
  onToggle,
  onInspect,
}: {
  turn: TurnView;
  filter: Filter;
  open: boolean;
  onToggle: () => void;
  onInspect?: () => void;
}) {
  const mine = turn.colour === "white";
  const align = mine ? "items-start" : "items-end";
  const showDetail = open && filter === "all";

  return (
    <div className="flex flex-col gap-2">
      {turn.san && (
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
          <span className="h-px bg-line" />
          <span className="tabular border border-accent-deep bg-surface px-2 py-0.5 font-mono text-[11px] text-accent">
            {moveLabel(turn.ply, turn.san)}
          </span>
          <span className="h-px bg-line" />
        </div>
      )}

      <div className={`flex flex-col gap-1.5 ${align}`}>
        <div
          className={`flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint ${
            mine ? "" : "flex-row-reverse"
          }`}
        >
          <i
            aria-hidden
            className={`block h-2 w-2 border border-line ${
              mine ? "bg-piece-white" : "bg-piece-black"
            }`}
          />
          {turn.model || turn.colour}
          {turn.live && <span className="text-accent">· thinking</span>}
        </div>

        {!turn.live && filter !== "talk" && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            className="inline-flex max-w-[94%] items-center gap-2 border border-line bg-surface px-2 py-1 font-mono text-[10px] text-ink-faint transition-colors hover:text-ink-dim"
          >
            <span aria-hidden className="text-machine">
              {open ? "▾" : "▸"}
            </span>
            {turn.illegal.length > 0 && (
              <span className="text-bad" title="illegal move attempts">
                !
              </span>
            )}
            <span className="text-ink-dim">
              {turn.tools.length} tool{turn.tools.length === 1 ? "" : "s"}
            </span>
            {turn.illegal.length > 0 && <span>· {turn.illegal.length} illegal</span>}
          </button>
        )}

        {/* Every number on this page traces to a payload; this is the link (LOG-07). */}
        {onInspect && open && filter !== "talk" && (
          <button
            type="button"
            onClick={onInspect}
            className="inline-flex items-center gap-1 border border-machine-deep bg-surface px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-machine transition-colors hover:border-machine hover:text-ink-dim"
          >
            raw transcript
          </button>
        )}

        {showDetail &&
          turn.reasoning.map((text, index) => (
            <p
              key={`${turn.key}-r${index}`}
              className={`max-w-[94%] text-xs leading-relaxed text-ink-dim ${
                mine
                  ? "border-l-2 border-machine-deep pl-2.5"
                  : "border-r-2 border-machine-deep pr-2.5 text-right"
              }`}
            >
              {text}
            </p>
          ))}

        {showDetail &&
          turn.tools.map((tool, index) => (
            <span
              key={`${turn.key}-t${index}`}
              className="inline-flex max-w-[94%] items-center gap-1.5 border border-machine-deep bg-surface px-2 py-0.5 font-mono text-[10px] text-machine"
            >
              {tool.name}()
            </span>
          ))}

        {showDetail &&
          turn.illegal.map((attempt, index) => (
            <p
              key={`${turn.key}-i${index}`}
              className="max-w-[94%] border border-bad-deep bg-surface px-2 py-1 font-mono text-[10px] leading-relaxed text-bad"
            >
              {attempt.move} → illegal · attempt {attempt.attempt}
              {attempt.detail && <span className="block text-ink-faint">{attempt.detail}</span>}
            </p>
          ))}

        {turn.said.map((message, index) => (
          <p
            key={`${turn.key}-s${index}`}
            className={`max-w-[94%] rounded-[13px] px-3 py-2 text-[13px] font-medium leading-snug text-on-accent ${
              mine ? "rounded-bl-[3px] bg-accent" : "rounded-br-[3px] bg-machine"
            }`}
          >
            {message}
          </p>
        ))}
      </div>
    </div>
  );
}

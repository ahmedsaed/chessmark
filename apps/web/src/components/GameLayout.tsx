"use client";

/**
 * The shape of a game page: board, conversation, stats.
 *
 * `LiveGame` and `Replay` both drew this grid, character for character, and a change to one had to
 * be remembered into the other. It is one component now because the phone layout below gives it
 * state, and state duplicated twice is state that drifts.
 *
 * **On a wide screen nothing has changed.** One expression does the whole layout: the board is
 * square, so its size is bounded by whichever runs out first — the height left under the page
 * chrome, or a reasonable share of the width. `min()` of those two is the board column; the two
 * rails are `1fr` each and split what is left, so there is never a dead gutter and never a
 * horizontal scrollbar. Deriving the board's width from its own height instead is the obvious idea
 * and does not work: in a grid an `auto` column must resolve its width before the row height is
 * known, and in flex the same knot ties itself the other way. Both were tried.
 *
 * **On a phone the three columns become a board and two tabs.** Stacked, they were board,
 * conversation, stats — and the stats were then a full conversation's scroll below the board, on
 * the one screen size where scrolling costs the most. Whose move it is, what it has cost and how
 * many illegal attempts each side has made are the things a spectator checks *between* moves, and
 * they were the least reachable thing on the page.
 *
 * Both panels stay mounted and are hidden with CSS rather than unmounted. `EventStream` holds the
 * reader's scroll position and which turn is expanded; unmounting it would hand back a panel
 * scrolled to the top every time somebody glanced at the stats and came back.
 */

import { useState } from "react";

type Tab = "events" | "info";

export function GameLayout({
  header,
  board,
  stream,
  stats,
  streamLabel = "Conversation",
  children,
}: {
  header: React.ReactNode;
  board: React.ReactNode;
  stream: React.ReactNode;
  stats: React.ReactNode;
  /** What the first tab is called. A replay scrubs a conversation; a live game watches one. */
  streamLabel?: string;
  /** Anything positioned against the whole layout — the promotion picker's overlay. */
  children?: React.ReactNode;
}) {
  const [tab, setTab] = useState<Tab>("events");

  return (
    <div className="flex flex-col gap-4">
      {header}

      <div className="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-9.5rem)] lg:grid-cols-[minmax(0,1fr)_min(calc(100dvh-12rem),52vw)_minmax(0,1fr)]">
        {/* **The `lg:order-*` values are the desktop layout, not decoration.** Stacked, the order
            is board, tabs, conversation, stats; side by side it is stats, board, conversation,
            because the middle column is the `min()`-sized one and the board is what it is sized
            for. Writing `lg:order-none` here instead put the board in a 323px rail and gave the
            conversation the 708px centre — three columns, correctly aligned, and the wrong thing
            in each. */}
        <div className="order-1 flex min-h-0 min-w-0 flex-col gap-2 lg:order-2">{board}</div>

        {/* Below the board, above both panels, and gone entirely at `lg` where both are visible
            at once and a tab would be a control that changes nothing. */}
        <div
          role="tablist"
          aria-label="Game panels"
          className="order-2 -mb-2 flex gap-px border border-line bg-line lg:hidden"
        >
          <TabButton active={tab === "events"} onClick={() => setTab("events")}>
            {streamLabel}
          </TabButton>
          <TabButton active={tab === "info"} onClick={() => setTab("info")}>
            Info
          </TabButton>
        </div>

        {/* **A bounded height, not a bounded number of turns.** Left to grow, the panel was as tall
            as the game was long — 300 plies of it — so reaching the newest turn meant scrolling
            past every older one, and the board scrolled off the top on the way. `EventStream`
            already scrolls inside itself; it only ever needed to be told how tall it is. `svh`
            rather than `vh` because a phone's URL bar makes `vh` taller than the screen. */}
        <div
          className={`order-3 h-[58svh] min-h-0 min-w-0 flex-col lg:order-3 lg:flex lg:h-auto ${
            tab === "events" ? "flex" : "hidden"
          }`}
        >
          {stream}
        </div>

        <div
          className={`order-4 min-h-0 min-w-0 overflow-y-auto lg:order-1 lg:block ${
            tab === "info" ? "block" : "hidden"
          }`}
        >
          {stats}
        </div>

        {children}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      /* `min-h-11` is the touch target, not the look: the rest of the site's controls are 25-28px
         tall, which is comfortable with a cursor and a guess with a thumb. */
      className={`flex min-h-11 flex-1 items-center justify-center font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
        active ? "bg-surface-2 text-ink" : "bg-surface text-ink-faint hover:text-ink-dim"
      }`}
    >
      {children}
    </button>
  );
}

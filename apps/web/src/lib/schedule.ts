/**
 * The schedule, newest first and shown a page at a time.
 *
 * A pool never ends, so its schedule only grows: `pool-free` was rendering several hundred
 * pairings into one column, every one of them a DOM node with a link and a title, under a
 * standings table that is the reason most people open the page. The interesting end of a list that
 * grows forever is the end that just changed, so the order is newest round first — and the first
 * page is therefore the **latest** matches, which is the part worth paying for on first paint.
 *
 * Pure, and separate from the component that renders it, because the fiddly parts are arithmetic:
 * the cut runs across *matches* while the display is grouped by *round*, so a page boundary
 * usually falls inside a round and that round has to appear twice — partly now, wholly later.
 */

import type { TournamentPairing } from "@/lib/types";

/** How many matches a page shows, and how many each "load more" adds. */
export const SCHEDULE_PAGE = 10;

export interface ScheduleRound {
  round: number;
  pairings: TournamentPairing[];
}

export interface SchedulePage {
  /** Rounds, newest first, holding only the matches on this page. */
  rounds: ScheduleRound[];
  /** How many matches are rendered. Never more than there are. */
  shown: number;
  /** How many are still hidden. Zero means the button has nothing left to do. */
  remaining: number;
}

/**
 * Take the newest `visible` matches and group them into rounds for rendering.
 *
 * The sort is by round only and `Array.prototype.sort` is stable, so pairings inside one round
 * keep the order the API sent them in — which is the order they were paired in. Re-sorting them
 * here would invent an ordering the API never promised.
 */
export function schedulePage(
  pairings: TournamentPairing[],
  visible: number = SCHEDULE_PAGE,
): SchedulePage {
  // A negative or fractional `visible` can only come from a bug, and the honest answer to one is
  // an empty page rather than `slice` quietly counting from the end of the array.
  const take = Math.max(0, Math.floor(visible));
  const newestFirst = [...pairings].sort((a, b) => b.round_number - a.round_number);
  const page = newestFirst.slice(0, take);

  const rounds: ScheduleRound[] = [];
  for (const pairing of page) {
    const last = rounds.at(-1);
    if (last && last.round === pairing.round_number) last.pairings.push(pairing);
    else rounds.push({ round: pairing.round_number, pairings: [pairing] });
  }

  return { rounds, shown: page.length, remaining: newestFirst.length - page.length };
}

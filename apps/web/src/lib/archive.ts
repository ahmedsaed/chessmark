/**
 * The archive's state, as it lives in the URL (UI-12).
 *
 * **Everything the `/games` page shows is a function of its query string**, so a filtered list is a
 * link somebody can send, the back button undoes a filter, and the page works with JavaScript off —
 * the filters are a plain `<form method="get">`. It also means each distinct query is one cached
 * read tagged `games` (ADR-0046), which is why this file is strict about what it accepts: an
 * unknown value is *dropped*, not passed along, so `?sort=banana&x=1&y=2…` cannot mint a fresh
 * cache entry per variation or reach the API as a 422.
 *
 * Two vocabularies meet here. The page's is written for a person reading the address bar —
 * `show=live`, `players=humans`, `vs=` — and the API's is written for the database. `apiQuery` is
 * the one place that translates, so neither has to bend towards the other.
 */

import type { GameStatus } from "@/lib/types";

export const SHOW = ["played", "live", "finished", "aborted", "all"] as const;
export const RESULTS = ["white", "black", "draw"] as const;
export const PLAYERS = ["models", "humans"] as const;
export const RANKED = ["ranked", "unranked"] as const;
export const SORTS = ["newest", "longest", "costliest"] as const;
/** Every `Termination` the API can return, in its order. `test_archive.py` fails if they drift. */
export const ENDINGS = [
  "checkmate",
  "stalemate",
  "threefold_repetition",
  "fifty_move_rule",
  "fivefold_repetition",
  "seventy_five_move_rule",
  "insufficient_material",
  "resignation",
  "agreed_draw",
  "illegal_move_forfeit",
  "error_forfeit",
  "truncated",
  "timeout",
  "budget_exceeded",
  "context_exceeded",
  "ply_cap",
  "adjudication",
  "abandoned",
] as const;

export type Show = (typeof SHOW)[number];
export type Result = (typeof RESULTS)[number];
export type Players = (typeof PLAYERS)[number];
export type Ranked = (typeof RANKED)[number];
export type Sort = (typeof SORTS)[number];
export type Ending = (typeof ENDINGS)[number];

export interface ArchiveFilter {
  show: Show;
  result?: Result;
  ending?: Ending;
  players?: Players;
  ranked?: Ranked;
  model?: string;
  vs?: string;
  event?: string;
  q?: string;
  sort: Sort;
  /**
   * The games after this one. Set only on the no-JavaScript path — "Load more" is a link to it —
   * because with JavaScript the next page is fetched and appended in place (`ArchiveList`).
   */
  before?: string;
}

/** How many games a page shows. The API is asked for one more, to learn whether there is a next. */
export const PAGE_SIZE = 50;

/** Matches the API's own ceiling, so a longer search is trimmed here rather than refused there. */
const MAX_QUERY = 100;

/**
 * **The default hides aborted games.** An abort is a harness failure — a provider that could not be
 * reached, a budget that ran out — and not a result (invariant 11). They were nearly half the local
 * archive, and a list that leads with them reads as a site that mostly fails. They are one filter
 * away, not gone.
 */
const STATUSES: Record<Show, GameStatus[]> = {
  played: ["pending", "running", "paused", "finished"],
  live: ["running", "paused"],
  finished: ["finished"],
  aborted: ["aborted"],
  all: [],
};

const API_RESULT: Record<Result, string> = {
  white: "white",
  black: "black",
  draw: "draw",
};

type Params = Record<string, string | string[] | undefined>;

function one(params: Params, key: string): string | undefined {
  const value = params[key];
  const first = Array.isArray(value) ? value[0] : value;
  const trimmed = first?.trim();
  return trimmed ? trimmed : undefined;
}

function oneOf<T extends string>(allowed: readonly T[], value: string | undefined): T | undefined {
  return allowed.find((candidate) => candidate === value);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * An OpenRouter id or a tournament slug. Loose on purpose — the registry decides what exists, and a
 * name that matches nothing is an empty list rather than an error — but bounded, so the URL cannot
 * carry an essay into a cache key.
 */
const IDENTIFIER = /^[\w.:@/-]{1,120}$/;

export function parseArchive(params: Params): ArchiveFilter {
  const ident = (key: string) => {
    const value = one(params, key);
    return value && IDENTIFIER.test(value) ? value : undefined;
  };
  const cursor = (key: string) => {
    const value = one(params, key);
    return value && UUID.test(value) ? value.toLowerCase() : undefined;
  };

  return {
    show: oneOf(SHOW, one(params, "show")) ?? "played",
    result: oneOf(RESULTS, one(params, "result")),
    ending: oneOf(ENDINGS, one(params, "ending")),
    players: oneOf(PLAYERS, one(params, "players")),
    ranked: oneOf(RANKED, one(params, "ranked")),
    model: ident("model"),
    vs: ident("vs"),
    event: ident("event"),
    q: one(params, "q")?.slice(0, MAX_QUERY),
    sort: oneOf(SORTS, one(params, "sort")) ?? "newest",
    before: cursor("before"),
  };
}

/** The API's query for this filter, asking for one row more than a page. */
export function apiQuery(filter: ArchiveFilter): URLSearchParams {
  const query = new URLSearchParams();
  for (const status of STATUSES[filter.show]) query.append("status", status);
  if (filter.result) query.set("result", API_RESULT[filter.result]);
  if (filter.ending) query.set("termination", filter.ending);
  if (filter.players) query.set("kind", filter.players);
  if (filter.ranked) query.set("ranked", String(filter.ranked === "ranked"));
  // `vs` alone means the same as `model` alone — one seat, either colour.
  const model = filter.model ?? filter.vs;
  if (model) query.set("model", model);
  if (filter.model && filter.vs) query.set("opponent", filter.vs);
  if (filter.event) query.set("tournament", filter.event);
  if (filter.q) query.set("q", filter.q);
  if (filter.sort !== "newest") query.set("sort", filter.sort);
  if (filter.before) query.set("before", filter.before);
  query.set("limit", String(PAGE_SIZE + 1));
  return query;
}

/**
 * The page's own URL for a filter, with every default left out.
 *
 * Left out rather than spelled, so the unfiltered archive is `/games` and not
 * `/games?show=played&sort=newest` — one address per list, and one cache entry.
 */
export function archiveHref(filter: ArchiveFilter): string {
  const query = new URLSearchParams();
  if (filter.q) query.set("q", filter.q);
  if (filter.show !== "played") query.set("show", filter.show);
  if (filter.result) query.set("result", filter.result);
  if (filter.ending) query.set("ending", filter.ending);
  if (filter.players) query.set("players", filter.players);
  if (filter.ranked) query.set("ranked", filter.ranked);
  if (filter.model) query.set("model", filter.model);
  if (filter.vs) query.set("vs", filter.vs);
  if (filter.event) query.set("event", filter.event);
  if (filter.sort !== "newest") query.set("sort", filter.sort);
  if (filter.before) query.set("before", filter.before);
  const search = query.toString();
  return search ? `/games?${search}` : "/games";
}

/**
 * The same filter with one thing changed, **back on the first page**.
 *
 * A cursor belongs to the list it was taken from. Carried into a different filter it names a game
 * that may not be in the new list at all, and "the page after a game you cannot see" is a page that
 * starts somewhere arbitrary.
 */
export function withFilter(filter: ArchiveFilter, change: Partial<ArchiveFilter>): string {
  return archiveHref({
    ...filter,
    before: undefined,
    ...change,
  });
}

/** Whether anything narrows the list beyond the default — what decides the "clear" link. */
export function isFiltered(filter: ArchiveFilter): boolean {
  return (
    archiveHref({
      ...filter,
      sort: "newest",
      before: undefined,
    }) !== "/games"
  );
}

export interface Page<T extends { id: string }> {
  games: T[];
  /** Whether the archive goes on past this page. */
  more: boolean;
}

/**
 * The `PAGE_SIZE + 1` rows the API returned, as a page and whether there is another.
 *
 * The extra row is the only way to know there is more without a `COUNT(*)` per filter; it is
 * dropped here and fetched again as the first row of the next page.
 */
export function paginate<T extends { id: string }>(rows: T[]): Page<T> {
  return { games: rows.slice(0, PAGE_SIZE), more: rows.length > PAGE_SIZE };
}

/**
 * Appends a page to what is already shown, leaving out any game already there.
 *
 * Under `longest` or `costliest` a running game's value rises between two loads and a keyset cursor
 * can hand the same game back (ADR-0048). Shown twice it would also be keyed twice, which React
 * rejects; dropping the repeat is the whole cost of the unstable sort.
 */
export function append<T extends { id: string }>(shown: T[], next: T[]): T[] {
  const seen = new Set(shown.map((game) => game.id));
  return [...shown, ...next.filter((game) => !seen.has(game.id))];
}

const ENDING_LABEL: Record<Ending, string> = {
  checkmate: "checkmate",
  resignation: "resignation",
  stalemate: "stalemate",
  threefold_repetition: "threefold repetition",
  fivefold_repetition: "fivefold repetition",
  fifty_move_rule: "fifty-move rule",
  seventy_five_move_rule: "seventy-five-move rule",
  insufficient_material: "insufficient material",
  agreed_draw: "agreed draw",
  illegal_move_forfeit: "illegal-move forfeit",
  error_forfeit: "error forfeit",
  // Out of output budget, repeatedly, without acting — a harness stop (ADR-0024), not a move cap.
  truncated: "truncated",
  timeout: "timeout",
  budget_exceeded: "budget exceeded",
  context_exceeded: "context exceeded",
  ply_cap: "move cap",
  adjudication: "adjudication",
  abandoned: "abandoned",
};

/** A termination as words. An ending this list does not know yet is shown as the API spells it. */
export function endingLabel(termination: string): string {
  return ENDING_LABEL[termination as Ending] ?? termination.replaceAll("_", " ");
}

/**
 * Whether the address that arrived is already the one `archiveHref` would write.
 *
 * Compared as a sorted set of pairs rather than as a string: the form and `archiveHref` agree on
 * field order, but a hand-typed URL need not, and `?sort=longest&q=x` is the same list as
 * `?q=x&sort=longest` — redirecting between them would be a round trip that changed nothing.
 */
export function isCanonical(params: Params): boolean {
  const arrived = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    for (const one of Array.isArray(value) ? value : [value]) {
      if (one !== undefined) arrived.append(key, one);
    }
  }
  const expected = new URLSearchParams(archiveHref(parseArchive(params)).split("?")[1] ?? "");
  arrived.sort();
  expected.sort();
  return arrived.toString() === expected.toString();
}

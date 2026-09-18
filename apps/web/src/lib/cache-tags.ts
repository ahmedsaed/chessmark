/**
 * The names the API invalidates this site's cached reads by.
 *
 * **The point of this file is that a clock is the wrong instrument.** Every read below changes on
 * exactly one occasion — a game writes a `game_events` row (invariant 7) — and the API knows the
 * moment it happens. A `revalidate` measured in seconds is a guess about that moment: too short and
 * the cache buys nothing, too long and the leaderboard is wrong for as long as the number says.
 * Tagging the reads and letting the worker name the tag makes the cache exact instead of lucky.
 *
 * The seconds below are therefore a **fallback, not the mechanism** — they bound how long a lost
 * revalidation POST can leave a page stale. A dropped notification is a few minutes of staleness,
 * not a permanent wrong answer.
 *
 * Both halves of the system have to agree on these strings, so they are spelled once here and
 * imported by `lib/api.ts` (which attaches them) and `app/api/revalidate/route.ts` (which acts on
 * them). The API sends the same names; `apps/api/src/chessmark/orchestration/revalidation.py` is
 * the other end and cites this file.
 */

/** Anything derived from the set of games: the lobby lists, the archive, the replay pool. */
export const GAMES = "games";

/** The stored ranking (ADR-0032) and the summary counts beside it. */
export const LEADERBOARD = "leaderboard";

/** The catalogue and every model's aggregates. */
export const MODELS = "models";

/** The tournament index and every event's table. */
export const TOURNAMENTS = "tournaments";

/**
 * One game's own record: its detail, its event log and its turns.
 *
 * Per-game rather than lumped into {@link GAMES} because a move in one game must not expire the
 * archive. A busy evening of ten concurrent games would otherwise invalidate every game page in
 * the site on every ply.
 */
export function game(id: string): string {
  return `game:${id}`;
}

/** Every tag the API is allowed to name. A request for anything else is refused, not obeyed. */
export const KNOWN_TAGS = [GAMES, LEADERBOARD, MODELS, TOURNAMENTS] as const;

/** A `game:<uuid>` tag, which is per-record and so cannot be enumerated above. */
const GAME_TAG = /^game:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Whether the API may invalidate this name.
 *
 * An allowlist rather than a pass-through: the revalidation endpoint takes a string from off the
 * process and hands it to `revalidateTag`, and a caller that could name any tag could expire the
 * whole cache on a loop. The shape of a game tag is checked rather than its existence — an unknown
 * id costs one no-op, a wildcard would cost the site.
 */
export function isKnownTag(tag: string): boolean {
  return (KNOWN_TAGS as readonly string[]).includes(tag) || GAME_TAG.test(tag);
}

/**
 * How long a tagged read may serve a stale answer if no invalidation ever arrives.
 *
 * Five minutes matches the social cards' own clock: it is long enough that the cache does real
 * work on a page nobody has asked for in a while, and short enough that a dropped POST is a
 * nuisance rather than an outage.
 */
export const FALLBACK_REVALIDATE = 300;

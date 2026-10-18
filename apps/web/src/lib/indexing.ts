/**
 * Which detail pages a search engine is asked to index — one rule, read by both the page's own
 * `robots` metadata and the sitemap, so the sitemap can never submit a page that then says
 * `noindex` (Search Console reports that as an error, and it is one).
 *
 * **Why any page is held back at all.** In October 2026 Search Console had 447 of the site's
 * pages "Discovered – currently not indexed": known, and not worth crawling. The sitemap was 484
 * URLs, 272 of them model pages for models that had never played — the same ~200 words of
 * template each — and many more were games that ended in a handful of plies to a forfeit. A crawler
 * that samples a new site and finds mostly that stops spending effort on the rest of it, including
 * the pages that are worth reading. These still render, link and share; they just stop asking to
 * be indexed. `noindex, follow`, so the pages they link to are still found.
 */

/** Below this a game is an opening and a forfeit, not a game anyone would search for. */
export const MIN_INDEXED_PLIES = 10;

export function gameIsIndexable(game: { ply_count: number }): boolean {
  return game.ply_count >= MIN_INDEXED_PLIES;
}

/**
 * A model page is worth indexing once the model has finished a game: before that it holds only
 * what the registry listing holds. `last_played_at` rather than `stats.games`, because the sitemap
 * reads the catalogue, which carries the date and not the stats — two tests would disagree about
 * a model whose only game is still being played.
 */
export function modelIsIndexable(model: { last_played_at?: string | null }): boolean {
  return Boolean(model.last_played_at);
}

/** What a held-back page says. Shared so the two routes cannot drift into `nofollow`. */
export const HELD_BACK = { index: false, follow: true } as const;

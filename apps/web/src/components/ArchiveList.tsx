"use client";

/**
 * The archive's rows, and "Load more" (UI-12).
 *
 * **The first page is the server's; every page after it is appended in place.** Older/newer links
 * replaced the whole list and threw the reader back to the top of it, so the next fifty games
 * arrived out of sight above the place they were reading. The button fetches the next page from the
 * API and adds it below, where the reader already is.
 *
 * **It is a link first.** Without JavaScript "Load more" is an `<a>` to `?before=<last id>`, the
 * server renders that page on its own, and nothing is lost but the appending. With JavaScript the
 * click is taken over.
 *
 * The browser asks the API directly — the same public origin `HeroGame` streams from — rather than
 * through a Next route: a server hop would add a request and a cache entry per page for a read the
 * API already answers in two statements. The address bar does not change, so a reload returns to
 * the first page, which is what a reload of a search result is expected to do.
 */

import { useState } from "react";

import { ArchiveRow } from "@/components/ArchiveRow";
import { PAGE_SIZE, append, paginate } from "@/lib/archive";
import type { GameSummary } from "@/lib/types";

export function ArchiveList({
  initial,
  more: initiallyMore,
  apiUrl,
  query,
  baseHref,
}: {
  initial: GameSummary[];
  more: boolean;
  /** The API's public origin. */
  apiUrl: string;
  /** The API query for this filter, without a cursor. */
  query: string;
  /**
   * This filter's own address, without a cursor. "Load more" without JavaScript goes to it plus
   * `before=` — a string rather than a function, because a server component cannot pass one.
   */
  baseHref: string;
}) {
  const [games, setGames] = useState(initial);
  const [more, setMore] = useState(initiallyMore);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const last = games.at(-1)?.id;

  async function loadMore(event: React.MouseEvent<HTMLAnchorElement>) {
    // Modified clicks keep their meaning: a new tab gets the server-rendered page.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0 || !last) return;
    event.preventDefault();
    if (busy) return;

    setBusy(true);
    setFailed(false);
    try {
      const params = new URLSearchParams(query);
      params.set("before", last);
      params.set("limit", String(PAGE_SIZE + 1));
      const response = await fetch(`${apiUrl}/games?${params}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) throw new Error(`GET /games failed: ${response.status}`);
      const page = paginate((await response.json()) as GameSummary[]);
      setGames((shown) => append(shown, page.games));
      setMore(page.more);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <ol className="mt-8 flex flex-col gap-px border border-line-soft bg-line-soft">
        {games.map((game) => (
          <ArchiveRow key={game.id} game={game} />
        ))}
      </ol>

      {more && last && (
        <div className="mt-6 flex flex-col items-center gap-2">
          <a
            href={`${baseHref}${baseHref.includes("?") ? "&" : "?"}before=${last}`}
            onClick={loadMore}
            aria-disabled={busy || undefined}
            className="border border-line bg-surface px-6 py-3 font-mono text-meta uppercase tracking-[0.14em] text-ink transition-colors hover:border-accent-dim hover:text-accent aria-disabled:text-ink-faint"
          >
            {busy ? "Loading…" : "Load more"}
          </a>
          {failed && (
            <p role="status" className="text-sm text-bad">
              The next games could not be loaded. Try again.
            </p>
          )}
        </div>
      )}
    </>
  );
}

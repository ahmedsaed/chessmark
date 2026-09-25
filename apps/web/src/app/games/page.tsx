import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { ArchiveFilters } from "@/components/ArchiveFilters";
import { ArchiveList } from "@/components/ArchiveList";
import { apiUrl, listArchive, listModels, listTournaments } from "@/lib/api";
import {
  apiQuery,
  archiveHref,
  isCanonical,
  isFiltered,
  paginate,
  parseArchive,
  withFilter,
} from "@/lib/archive";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Games",
  description:
    "Every game Chessmark has played — filter by result, ending, model or event, and open any of them.",
  path: "/games",
});

/**
 * The archive (UI-12).
 *
 * **Complete in one response, like every other page.** It reads its filters from the query string,
 * and each distinct filter is one cached read tagged `games` (ADR-0046) — nothing here streams,
 * and nothing is fetched in the browser. The ADR that argues for filtering on the server rather
 * than in memory, as `/models` does, is ADR-0048.
 */
export default async function GamesPage({ searchParams }: PageProps<"/games">) {
  const params = await searchParams;
  const filter = parseArchive(params);

  /* **One address per list.** A form without JavaScript submits every field, empty ones and
     defaults included, and a hand-edited URL can carry anything. Both land here and are sent to the
     canonical spelling, so a shared link, the cache and the back button all see the same thing. */
  const canonical = archiveHref(filter);
  if (!isCanonical(params)) redirect(canonical);

  const [rows, models, tournaments] = await Promise.all([
    listArchive(apiQuery(filter)),
    listModels(),
    listTournaments(),
  ]);
  const page = rows ? paginate(rows) : null;
  const cursorless = apiQuery({ ...filter, before: undefined });

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">Games</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-dim">
        Every game played here, newest first. Aborted games — a provider that could not be reached,
        a budget that ran out — are not results and are hidden unless you ask for them.
      </p>

      <div className="mt-8">
        {/* Keyed on the address: the inputs hold `defaultValue`s, which are read once, so after a
            client navigation — "clear filters", a link from another page — they would keep showing
            the filter the reader just left. */}
        <ArchiveFilters
          key={canonical}
          filter={filter}
          models={models.map((model) => ({
            id: model.openrouter_id,
            name: model.display_name,
          }))}
          events={tournaments.map((event) => ({
            slug: event.slug,
            name: event.name,
          }))}
        />
      </div>

      {isFiltered(filter) && (
        <p className="mt-3 font-mono text-meta uppercase tracking-[0.14em]">
          <Link href="/games" className="text-accent underline-offset-4 hover:underline">
            Clear filters
          </Link>
        </p>
      )}

      {page === null ? (
        <p className="mt-8 border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          The archive could not be read just now. The games are safe; try again in a moment.
        </p>
      ) : page.games.length === 0 ? (
        <p className="mt-8 border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          {isFiltered(filter) ? (
            <>
              No games match these filters.{" "}
              <Link href="/games" className="text-accent underline-offset-4 hover:underline">
                Clear them
              </Link>
              .
            </>
          ) : (
            "No games yet."
          )}
        </p>
      ) : (
        <>
          <ArchiveList
            // Keyed on the address, so a new filter starts a new list instead of appending to
            // the last one's.
            key={canonical}
            initial={page.games}
            more={page.more}
            apiUrl={apiUrl}
            query={cursorless.toString()}
            baseHref={withFilter(filter, {})}
          />
          {filter.before && (
            <p className="mt-4 text-center font-mono text-meta uppercase tracking-[0.14em]">
              <Link
                href={withFilter(filter, {})}
                className="text-accent underline underline-offset-4"
              >
                Back to the newest
              </Link>
            </p>
          )}
        </>
      )}
    </main>
  );
}

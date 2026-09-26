import type { Metadata } from "next";
import Link from "next/link";

import { listTournaments } from "@/lib/api";
import type { TournamentSummary } from "@/lib/types";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Tournaments",
  description:
    "Automated events: round robins and Swiss, run unattended against a budget.",
  path: "/tournaments",
  // `tournaments/opengraph-image.tsx` names the running event.
  hasOwnImage: true,
});

export default async function TournamentsPage() {
  const tournaments = await listTournaments();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">Tournaments</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-dim">
        A field, a format, and a budget. Every bracket — the free models, open weights against
        closed, one vendor&rsquo;s catalogue — is the same machinery with a different filter.
      </p>

      {tournaments.length === 0 ? (
        <p className="mt-10 border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          No tournaments yet. One is created from the command line and ticked along by a worker;
          when the first is running it will appear here.
        </p>
      ) : (
        <ul className="mt-8 flex flex-col gap-px border border-line-soft bg-line-soft">
          {tournaments.map((tournament) => (
            <li key={tournament.id}>
              <Link
                href={`/tournaments/${tournament.slug}`}
                className="flex flex-col gap-1 bg-surface px-4 py-3 transition-colors hover:bg-surface-2"
              >
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="font-serif text-lg text-ink">{tournament.name}</span>
                  <StatusChip status={tournament.status} />
                  <span className="tabular ml-auto font-mono text-meta text-ink-faint">
                    {tournament.stats.played} / {tournament.stats.pairings} played
                  </span>
                </div>
                <p className="font-mono text-meta text-ink-faint">
                  {tournament.field_description} · {tournament.entrant_count} entrants ·{" "}
                  {formatLabel(tournament)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

export function formatLabel(tournament: TournamentSummary): string {
  if (tournament.format === "swiss") return `Swiss, ${tournament.rounds} rounds`;
  /* **A pool said it was a "round robin"**, because this only knew two formats and fell through to
     the second. It pairs *like* one — greedy and incremental, every pair before any repeat
     (ADR-0041) — but what a reader needs from the word is that it never ends and its field is not
     fixed, which "round robin" states the opposite of. */
  /* A pool with a per-pair target does end its pairs — it plays each one that many times and then
     waits for a newcomer (ADR-0050) — so "never ends" would now be untrue of it. */
  if (tournament.format === "pool" && tournament.games_per_pair) {
    const n = tournament.games_per_pair;
    return `pool · ${n} game${n === 1 ? "" : "s"} a pair`;
  }
  if (tournament.format === "pool") return "pool · never ends";
  return tournament.double ? "double round robin" : "round robin";
}

/** Colour carries the state, so a glance is enough. */
export function StatusChip({ status }: { status: string }) {
  const tone =
    status === "running"
      ? "border-accent text-accent"
      : status === "finished"
        ? "border-good/50 text-good"
        : status === "paused"
          ? "border-bad/50 text-bad"
          : "border-line text-ink-faint";

  return (
    <span
      className={`border px-1.5 py-px font-mono text-label uppercase tracking-[0.14em] ${tone}`}
    >
      {status}
    </span>
  );
}

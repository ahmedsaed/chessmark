import type { Metadata } from "next";
import Link from "next/link";

import { getLeaderboard } from "@/lib/api";
import type { LeaderboardRow } from "@/lib/types";
import { pageMetadata } from "@/lib/site";
import { RuntimeBadge } from "@/components/RuntimeBadge";

export const metadata: Metadata = pageMetadata({
  title: "Leaderboard",
  description:
    "Glicko-2 ratings for language models playing chess, with illegal-move rates and every excluded game listed.",
  path: "/leaderboard",
  // `leaderboard/opengraph-image.tsx` draws the ranking itself.
  hasOwnImage: true,
});

function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount === 0) return "—";
  return amount < 0.001 ? `$${amount.toFixed(5)}` : `$${amount.toFixed(3)}`;
}

export default async function LeaderboardPage() {
  const board = await getLeaderboard();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <header className="flex flex-col gap-4 border-b border-line pb-8">
        <h1 className="font-serif text-4xl leading-tight text-ink">Leaderboard</h1>
        <p className="leading-relaxed text-ink-dim">
          Glicko-2 over ranked games. A contestant is a model <em>at a precision</em> — the same
          weights served at 4-bit and at 8-bit are different entrants and are ranked apart.
        </p>
        <p className="tabular font-mono text-data text-ink-faint">
          {board.games_counted} game{board.games_counted === 1 ? "" : "s"} counted ·{" "}
          {board.excluded.length} excluded · prompt {board.prompt_version ?? "—"}
        </p>
      </header>

      {board.rows.length === 0 ? (
        <p className="mt-10 text-sm leading-relaxed text-ink-dim">
          No ranked games yet. Ratings only move on games played in the ranked configuration —
          fixed prompt version, trash talk off, one pinned endpoint per seat. Everything else is
          still recorded and replayable, it just does not count.
        </p>
      ) : (
        <Table rows={board.rows} />
      )}

      <Excluded excluded={board.excluded} counted={board.games_counted} />

      <p className="mt-10 border-t border-line pt-6 text-sm text-ink-dim">
        <Link href="/methodology" className="text-accent underline-offset-4 hover:underline">
          How this ranking works, and where it is weak →
        </Link>
      </p>
    </main>
  );
}

function Table({ rows }: { rows: LeaderboardRow[] }) {
  /* **On a phone the table scrolls, and `#` and the contestant stay put.** The secondary columns
     used to be hidden below `sm`, because before that the whole 820px table scrolled and a phone at
     rest showed `#` and `Contestant` with the rating off to the right and nothing saying so. Hiding
     fixed that and made W/D/L, the illegal-move rate, cost and latency unreachable on a phone.

     The pinned block is 60% of the scroller (`cqw`), which leaves the rating whole at rest and the
     edge of W/D/L showing — the only sign there is more. Pinning the rating too was tried and left
     a 3.5rem window that no column wider than that could ever be read through.

     Below `sm` every pinned cell needs three things or the pinning breaks: a solid background
     (the scrolled columns show through without one), a fixed width (a sticky offset is a
     constant), and borders on the cells rather than the row — under `border-collapse` the row's
     line is the table's to paint, and a sticky cell drawn over it cut it in half. */
  const stuck = "sticky z-[1] bg-ground group-hover:bg-surface-2 sm:static sm:bg-transparent";
  return (
    <div className="@container mt-8 overflow-x-auto border border-line">
      <table className="w-max min-w-full border-separate border-spacing-0 text-left sm:w-full sm:min-w-[820px] [&_td]:border-b [&_td]:border-line-soft [&_tr:last-child_td]:border-0">
        <thead>
          <tr className="font-mono text-label uppercase tracking-[0.12em] text-ink-faint [&_th]:border-b [&_th]:border-line [&_th]:bg-surface-3">
            <th className={`${stuck} left-0 w-8 px-2 py-2 font-normal sm:w-auto sm:px-3`}>#</th>
            <th
              className={`${stuck} left-8 px-2 py-2 font-normal shadow-[inset_-1px_0_0_var(--color-line)] sm:px-3 sm:shadow-none`}
            >
              Contestant
            </th>
            <th
              className="w-24 px-2 py-2 text-right font-normal sm:w-auto sm:px-3"
              title="Glicko-2 rating and deviation"
            >
              Rating
            </th>
            <th className="px-3 py-2 text-right font-normal">W/D/L</th>
            <th
              className="px-3 py-2 text-right font-normal"
              title="Illegal move attempts per move played — the benchmark's headline number"
            >
              Illegal/move
            </th>
            <th className="px-3 py-2 text-right font-normal">Forfeits</th>
            <th className="px-3 py-2 text-right font-normal">Cost/game</th>
            <th className="px-3 py-2 text-right font-normal">Latency</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.model_id}-${row.quantization}`} className="group hover:bg-surface-2">
              <td
                className={`${stuck} left-0 w-8 px-2 py-2.5 font-mono text-data text-ink-faint tabular sm:px-3`}
              >
                {index + 1}
              </td>
              <td
                className={`${stuck} left-8 px-2 py-2.5 shadow-[inset_-1px_0_0_var(--color-line)] sm:px-3 sm:shadow-none`}
              >
                {/* A width on the cell's content, not the cell: a table cell grows to the
                    min-content of what is inside it, and a `truncate` slug's min-content is the
                    whole slug — 463px in a 333px scroller, the bug the first phone pass had. */}
                <div className="w-[calc(60cqw-3rem)] sm:w-auto">
                  {/* Drills through to the games that produced the row (BENCH-02). */}
                  {/* `prefetch={false}` because a visible link to `/models/[...slug]` prefetches a
                      payload the router cannot store — every route here is dynamic, so it comes
                      back `no-store` — and reschedules itself for as long as it is on screen. This
                      page was serving ~90 requests in twelve seconds per row on production
                      (FRONTEND.md). */}
                  <Link
                    prefetch={false}
                    href={`/models/${row.model_slug}#c-${encodeURIComponent(row.quantization)}`}
                    className="block truncate font-mono text-xs text-ink transition-colors hover:text-accent sm:inline"
                  >
                    {row.model_slug}
                  </Link>
                  <span className="mt-0.5 inline-block border border-good/40 px-1 py-px font-mono text-label uppercase tracking-wider text-good sm:ml-1.5 sm:mt-0">
                    {row.quantization}
                  </span>
                  <RuntimeBadge runtime={row.runtime} className="ml-1.5 mt-0.5 sm:mt-0" />
                </div>
              </td>
              <td
                className="w-24 whitespace-nowrap px-2 py-2.5 text-right font-mono text-xs text-ink tabular sm:px-3"
              >
                {Math.round(row.rating)}
                {/* The `?` is the deviation said in a word. "± 208" is honest and most readers
                    cannot act on it; the mark is the same fact in a form they can. The number
                    stays, in the title, for readers who do think in deviations. */}
                {row.provisional && (
                  <span
                    className="ml-0.5 text-ink-faint"
                    title={`Provisional — too few games to settle this rating (± ${Math.round(row.rating_deviation)})`}
                  >
                    ?
                  </span>
                )}
                {/* The deviation is not decoration: it is what stops a three-game rating being
                    read as a three-hundred-game one. */}
                {/* The deviation is the first thing to go on a narrow screen: the `?` above already
                    says "provisional", which is the part a reader acts on, and the pinned block
                    has no width to spare for a number its title already carries. */}
                <span className="ml-1 hidden text-ink-faint sm:inline">
                  ± {Math.round(row.rating_deviation)}
                </span>
              </td>
              <td className="tabular px-3 py-2.5 text-right font-mono text-xs text-ink-dim">
                {row.wins}/{row.draws}/{row.losses}
              </td>
              {/* A decision model is offered only legal moves (ADR-0049), so its zero is not a
                  result — a dash, rather than a green 0.000 that reads as one. */}
              {row.runtime === "decision" ? (
                <td
                  className="tabular px-3 py-2.5 text-right font-mono text-xs text-ink-faint"
                  title="A decision model is offered only legal moves, so it cannot play an illegal one"
                >
                  —
                </td>
              ) : (
                <td
                  className={`tabular px-3 py-2.5 text-right font-mono text-xs ${
                    row.illegal_per_move > 0 ? "text-bad" : "text-good"
                  }`}
                  title={`${row.illegal_attempts} attempts over ${row.moves_played} moves`}
                >
                  {row.illegal_per_move.toFixed(3)}
                </td>
              )}
              <td
                className={`tabular px-3 py-2.5 text-right font-mono text-xs ${
                  row.forfeits > 0 ? "text-bad" : "text-ink-faint"
                }`}
              >
                {row.forfeits}
              </td>
              <td className="tabular px-3 py-2.5 text-right font-mono text-xs text-ink-dim">
                {usd(row.mean_cost_usd)}
              </td>
              <td className="tabular px-3 py-2.5 text-right font-mono text-xs text-ink-faint">
                {row.mean_latency_ms > 0 ? `${(row.mean_latency_ms / 1000).toFixed(1)}s` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Every game that did not count, with its reason.
 *
 * On the leaderboard rather than buried in the methodology page. A ranking that silently drops
 * games is indistinguishable from one that is wrong, and the honest fix is to show the count where
 * the numbers are (BENCH-10).
 */
function Excluded({ excluded, counted }: { excluded: { game_id: string; reason: string }[]; counted: number }) {
  if (excluded.length === 0) return null;

  const byReason = new Map<string, string[]>();
  for (const game of excluded) {
    byReason.set(game.reason, [...(byReason.get(game.reason) ?? []), game.game_id]);
  }

  return (
    <section className="mt-10">
      <h2 className="mb-3 font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">
        Excluded · {excluded.length} of {excluded.length + counted} finished games
      </h2>
      <ul className="flex flex-col gap-1.5">
        {[...byReason.entries()]
          .sort((a, b) => b[1].length - a[1].length)
          .map(([reason, ids]) => (
            <li key={reason} className="flex flex-wrap items-baseline gap-2 text-xs">
              <span className="tabular font-mono text-ink">{ids.length}×</span>
              <span className="text-ink-dim">{reason}</span>
              {/* **`py-1` and a wider gap are a tap target, not padding.** These are 10px ids in a
                  wrapped row, and with production data — where most reasons have four of them —
                  Lighthouse scored `target-size` at zero: a 13px-tall link with 6px between it and
                  the next is a guess with a finger. The local seed has too few excluded games for
                  the audit to see it, which is why it survived. */}
              <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                {ids.slice(0, 4).map((id) => (
                  <Link
                    key={id}
                    href={`/games/${id}`}
                    className="inline-flex min-h-6 items-center py-1 font-mono text-meta text-ink-faint underline-offset-4 hover:text-accent hover:underline"
                  >
                    {id.slice(0, 8)}
                  </Link>
                ))}
                {ids.length > 4 && (
                  <span className="font-mono text-meta text-ink-faint">
                    +{ids.length - 4} more
                  </span>
                )}
              </span>
            </li>
          ))}
      </ul>
    </section>
  );
}

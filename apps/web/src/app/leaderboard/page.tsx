import Link from "next/link";

import { getLeaderboard } from "@/lib/api";
import type { LeaderboardRow } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Leaderboard",
  description:
    "Glicko-2 ratings for language models playing chess, with illegal-move rates and every excluded game listed.",
};

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
        <p className="max-w-prose text-ink-dim">
          Glicko-2 over ranked games. A contestant is a model <em>at a precision</em> — the same
          weights served at 4-bit and at 8-bit are different entrants and are ranked apart.
        </p>
        <p className="tabular font-mono text-[11px] text-ink-faint">
          {board.games_counted} game{board.games_counted === 1 ? "" : "s"} counted ·{" "}
          {board.excluded.length} excluded · prompt {board.prompt_version ?? "—"}
        </p>
      </header>

      {board.rows.length === 0 ? (
        <p className="mt-10 max-w-prose text-sm leading-relaxed text-ink-dim">
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
  return (
    /* Scrolls inside itself rather than pushing the page sideways on a phone. */
    <div className="mt-8 overflow-x-auto border border-line">
      <table className="w-full min-w-[820px] border-collapse text-left">
        <thead>
          <tr className="border-b border-line bg-surface-3 font-mono text-[9.5px] uppercase tracking-[0.12em] text-ink-faint">
            <th className="px-3 py-2 font-normal">#</th>
            <th className="px-3 py-2 font-normal">Contestant</th>
            <th className="px-3 py-2 text-right font-normal" title="Glicko-2 rating and deviation">
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
            <tr
              key={`${row.model_id}-${row.quantization}`}
              className="border-b border-line-soft last:border-0 hover:bg-surface-2"
            >
              <td className="tabular px-3 py-2.5 font-mono text-[11px] text-ink-faint">
                {index + 1}
              </td>
              <td className="px-3 py-2.5">
                {/* Drills through to the games that produced the row (BENCH-02). */}
                <Link
                  href={`/leaderboard/${encodeURIComponent(row.model_slug)}?q=${row.quantization}`}
                  className="font-mono text-xs text-ink transition-colors hover:text-accent"
                >
                  {row.model_slug}
                </Link>
                <span className="ml-1.5 border border-good/40 px-1 py-px font-mono text-[8.5px] uppercase tracking-wider text-good">
                  {row.quantization}
                </span>
              </td>
              <td className="tabular px-3 py-2.5 text-right font-mono text-xs text-ink">
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
                <span className="ml-1 text-ink-faint">± {Math.round(row.rating_deviation)}</span>
              </td>
              <td className="tabular px-3 py-2.5 text-right font-mono text-xs text-ink-dim">
                {row.wins}/{row.draws}/{row.losses}
              </td>
              <td
                className={`tabular px-3 py-2.5 text-right font-mono text-xs ${
                  row.illegal_per_move > 0 ? "text-bad" : "text-good"
                }`}
                title={`${row.illegal_attempts} attempts over ${row.moves_played} moves`}
              >
                {row.illegal_per_move.toFixed(3)}
              </td>
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
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        Excluded · {excluded.length} of {excluded.length + counted} finished games
      </h2>
      <ul className="flex flex-col gap-1.5">
        {[...byReason.entries()]
          .sort((a, b) => b[1].length - a[1].length)
          .map(([reason, ids]) => (
            <li key={reason} className="flex flex-wrap items-baseline gap-2 text-xs">
              <span className="tabular font-mono text-ink">{ids.length}×</span>
              <span className="text-ink-dim">{reason}</span>
              <span className="flex flex-wrap gap-1.5">
                {ids.slice(0, 4).map((id) => (
                  <Link
                    key={id}
                    href={`/games/${id}`}
                    className="font-mono text-[10px] text-ink-faint underline-offset-4 hover:text-accent hover:underline"
                  >
                    {id.slice(0, 8)}
                  </Link>
                ))}
                {ids.length > 4 && (
                  <span className="font-mono text-[10px] text-ink-faint">
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

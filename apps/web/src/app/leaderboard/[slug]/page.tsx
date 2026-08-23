import Link from "next/link";

import { getContestantGames, getLeaderboard } from "@/lib/api";
import type { GameSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * The games behind one leaderboard row.
 *
 * The drill-down criterion: every published number reaches the games that produced it. A ranking
 * whose rows cannot be opened is asking to be taken on faith, which is the one thing a benchmark
 * cannot ask for.
 */
export default async function ContestantPage({
  params,
  searchParams,
}: PageProps<"/leaderboard/[slug]">) {
  const { slug } = await params;
  const { q } = await searchParams;
  const quantization = typeof q === "string" ? q : undefined;
  const modelSlug = decodeURIComponent(slug);

  const [games, board] = await Promise.all([
    getContestantGames(modelSlug, quantization),
    getLeaderboard(),
  ]);

  const row = board.rows.find(
    (candidate) =>
      candidate.model_slug === modelSlug &&
      (quantization === undefined || candidate.quantization === quantization),
  );

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-10">
      <Link
        href="/leaderboard"
        className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-faint transition-colors hover:text-accent"
      >
        ← Leaderboard
      </Link>

      <h1 className="mt-4 font-mono text-2xl text-ink">
        {modelSlug}
        {quantization && (
          <span className="ml-2 border border-good/40 px-1.5 py-0.5 align-middle font-mono text-[11px] uppercase tracking-wider text-good">
            {quantization}
          </span>
        )}
      </h1>

      {row && (
        <dl className="mt-4 grid grid-cols-2 gap-px border border-line-soft bg-line-soft sm:grid-cols-4">
          <Stat label="Rating" value={`${Math.round(row.rating)} ± ${Math.round(row.rating_deviation)}`} />
          <Stat label="W / D / L" value={`${row.wins} / ${row.draws} / ${row.losses}`} />
          <Stat
            label="Illegal per move"
            value={row.illegal_per_move.toFixed(3)}
            tone={row.illegal_per_move > 0 ? "bad" : "good"}
          />
          <Stat label="Forfeits" value={String(row.forfeits)} tone={row.forfeits > 0 ? "bad" : undefined} />
        </dl>
      )}

      <h2 className="mt-10 mb-3 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        {games.length} rated game{games.length === 1 ? "" : "s"}
      </h2>

      {games.length === 0 ? (
        <p className="text-sm text-ink-dim">
          No rated games. Every game this contestant played was excluded — see the reasons on the{" "}
          <Link href="/leaderboard" className="text-accent underline-offset-4 hover:underline">
            leaderboard
          </Link>
          .
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {games.map((game) => (
            <GameRow key={game.id} game={game} slug={modelSlug} />
          ))}
        </ul>
      )}
    </main>
  );
}

function GameRow({ game, slug }: { game: GameSummary; slug: string }) {
  const me = game.players.find((player) => player.model === slug);
  const other = game.players.find((player) => player.model !== slug);
  const won =
    (me?.colour === "white" && game.result === "1-0") ||
    (me?.colour === "black" && game.result === "0-1");
  const drew = game.result === "1/2-1/2";

  return (
    <li>
      <Link
        href={`/games/${game.id}`}
        className="flex flex-col gap-2 border border-line bg-surface-2 p-3 transition-colors hover:border-accent-dim"
      >
        <div className="flex items-center justify-between gap-3">
          <span className="truncate font-mono text-xs text-ink">
            as {me?.colour ?? "?"} <span className="text-ink-faint">vs</span>{" "}
            {other?.model ?? "?"}
          </span>
          <span
            className={`flex-none font-mono text-[11px] ${
              drew ? "text-ink-dim" : won ? "text-good" : "text-bad"
            }`}
          >
            {drew ? "draw" : won ? "win" : "loss"}
          </span>
        </div>
        <p className="tabular font-mono text-[10px] text-ink-faint">
          {game.ply_count} plies · {game.termination}
          {me && me.illegal_attempts > 0 ? ` · ${me.illegal_attempts} illegal` : ""}
        </p>
      </Link>
    </li>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const colour = tone === "bad" ? "text-bad" : tone === "good" ? "text-good" : "text-ink";
  return (
    <div className="bg-surface px-3 py-2">
      <dt className="font-mono text-[9px] uppercase tracking-[0.12em] text-ink-faint">{label}</dt>
      <dd className={`tabular mt-1 font-mono text-sm ${colour}`}>{value}</dd>
    </div>
  );
}

import Link from "next/link";

import { listGames, listModels } from "@/lib/api";
import type { GameSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [live, recent, models] = await Promise.all([
    listGames("running", 6),
    listGames(undefined, 12),
    listModels(true),
  ]);

  const finished = recent.filter((game) => game.status !== "running").slice(0, 8);

  return (
    <main className="mx-auto w-full max-w-[1180px] px-5 py-10">
      <header className="flex flex-col gap-4 border-b border-line pb-8">
        <p className="font-mono text-xs uppercase tracking-[0.22em] text-accent">Chessmark</p>
        <h1 className="max-w-2xl font-serif text-4xl leading-tight text-ink sm:text-5xl">
          Language models play chess.
          <br />
          <span className="text-accent">Everything is recorded.</span>
        </h1>
        <p className="max-w-prose text-ink-dim">
          Agents move through tools, keep a transcript across the whole game, and trash-talk each
          other while they do it. Every request, reasoning trace, tool call, and taunt is stored
          and replayable.
        </p>
      </header>

      <Section title="Live now" empty="No games running. Start one from the API or `make play`.">
        {live.map((game) => (
          <GameCard key={game.id} game={game} />
        ))}
      </Section>

      <Section title="Recent" empty="Nothing finished yet.">
        {finished.map((game) => (
          <GameCard key={game.id} game={game} />
        ))}
      </Section>

      <section className="mt-12">
        <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
          Playable models · {models.length} free, tool-capable
        </h2>
        <ul className="grid grid-cols-1 gap-px border border-line-soft bg-line-soft sm:grid-cols-2 lg:grid-cols-3">
          {models.map((model) => (
            <li key={model.id} className="bg-surface px-4 py-3">
              <p className="truncate font-mono text-xs text-ink">{model.openrouter_id}</p>
              <p className="tabular mt-1 font-mono text-[10px] text-ink-faint">
                {model.provider}
                {model.context_length
                  ? ` · ${Math.round(model.context_length / 1000)}k ctx`
                  : ""}
                {model.supports_reasoning ? " · reasoning" : ""}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function Section({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: React.ReactNode;
}) {
  const items = Array.isArray(children) ? children : [children];
  const isEmpty = items.flat().filter(Boolean).length === 0;

  return (
    <section className="mt-10">
      <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        {title}
      </h2>
      {isEmpty ? (
        <p className="font-mono text-xs text-ink-faint">{empty}</p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">{children}</ul>
      )}
    </section>
  );
}

function GameCard({ game }: { game: GameSummary }) {
  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");
  const running = game.status === "running";
  const illegal = game.players.reduce((total, p) => total + p.illegal_attempts, 0);

  return (
    <li>
      <Link
        href={`/games/${game.id}`}
        className="flex flex-col gap-2.5 border border-line bg-surface-2 p-4 transition-colors hover:border-accent-dim focus-visible:border-accent"
      >
        <div className="flex items-center justify-between gap-3">
          <span className="truncate font-mono text-xs text-ink">
            {white?.display_name ?? "?"} <span className="text-ink-faint">vs</span>{" "}
            {black?.display_name ?? "?"}
          </span>
          {running ? (
            <span className="inline-flex flex-none items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.14em] text-bad">
              <i aria-hidden className="block h-1.5 w-1.5 animate-pulse rounded-full bg-bad" />
              live
            </span>
          ) : (
            <span className="tabular flex-none font-mono text-[11px] text-accent">
              {game.result}
            </span>
          )}
        </div>

        <p className="tabular font-mono text-[10px] text-ink-faint">
          {game.ply_count} plies
          {game.termination ? ` · ${game.termination}` : ""}
          {illegal > 0 ? ` · ${illegal} illegal` : ""}
          {game.is_ranked ? " · ranked" : ""}
        </p>
      </Link>
    </li>
  );
}

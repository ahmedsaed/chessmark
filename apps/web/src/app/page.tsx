import Link from "next/link";

import { AccountBar } from "@/components/AccountBar";
import { NewGameSection } from "@/components/NewGameSection";
import { apiUrl, listGames, listModels } from "@/lib/api";
import type { GameSummary, ModelInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [live, recent, models] = await Promise.all([
    listGames("running", 6),
    listGames(undefined, 12),
    listModels(),
  ]);

  const finished = recent.filter((game) => game.status !== "running").slice(0, 8);

  return (
    <main className="mx-auto w-full max-w-[1180px] px-5 py-10">
      <header className="flex flex-col gap-4 border-b border-line pb-8">
        <div className="flex items-center gap-3">
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-accent">Chessmark</p>
          <Link
            href="/leaderboard"
            className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-faint transition-colors hover:text-accent"
          >
            Leaderboard
          </Link>
          <AccountBar apiUrl={apiUrl} />
        </div>
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

      <NewGameSection apiUrl={apiUrl} models={models} />

      <Section title="Live now" empty="No games running. Start one above, or with `make play`.">
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
          Contestants · {models.length} models ·{" "}
          {models.reduce((total, m) => total + m.contestants.length, 0)} entrants
        </h2>
        <ul className="grid grid-cols-1 gap-px border border-line-soft bg-line-soft sm:grid-cols-2 lg:grid-cols-3">
          {models.slice(0, 24).map((model) => (
            <li key={model.id} className="bg-surface px-4 py-3">
              <p className="truncate font-mono text-xs text-ink">{model.openrouter_id}</p>
              <p className="tabular mt-1 font-mono text-[10px] text-ink-faint">
                {model.provider}
                {model.context_length
                  ? ` · ${Math.round(model.context_length / 1000)}k ctx`
                  : ""}
                {model.supports_reasoning ? " · reasoning" : ""}
                {model.is_floating_alias ? " · unrankable" : ""}
              </p>
              <Contestants model={model} />
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

/**
 * A model's contestants: one per precision, each naming the endpoint that would serve it.
 *
 * This used to show which precisions were *allowed*, because the policy was a filter. It is not
 * one any more — `model@fp4` and `model@fp8` are separate entrants, ranked apart (ADR-0015) — so
 * the card lists entrants rather than permissions, and names the endpoint, because the endpoint
 * turned out to change results as much as the precision does.
 */
function Contestants({ model }: { model: ModelInfo }) {
  if (model.contestants.length === 0) {
    return (
      <p className="mt-1.5 font-mono text-[9px] uppercase tracking-wider text-ink-faint">
        no tool-capable endpoint
      </p>
    );
  }

  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {model.contestants.map((entrant) => (
        <li
          key={entrant.quantization}
          className="flex flex-wrap items-center gap-1.5 font-mono text-[9px]"
        >
          <span
            title={`played at ${entrant.quantization} — its own leaderboard entry`}
            className="border border-good/40 px-1 py-px uppercase tracking-wider text-good"
          >
            {entrant.quantization}
          </span>
          <span className="text-ink-dim" title="the endpoint a game would pin">
            {entrant.provider}
          </span>
          {entrant.uptime_1d !== null && (
            <span
              className="tabular text-ink-faint"
              title="uptime over the last day — how the endpoint is chosen"
            >
              {entrant.uptime_1d.toFixed(1)}%
            </span>
          )}
          {entrant.endpoint_count === 1 && (
            <span title="only one endpoint serves this precision" className="text-ink-faint">
              sole
            </span>
          )}
        </li>
      ))}
    </ul>
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

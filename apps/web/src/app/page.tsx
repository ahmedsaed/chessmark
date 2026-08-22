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
          Playable models · {models.length} tool-capable ·{" "}
          {models.filter((m) => m.playable_quantizations.length === 0).length} blocked on precision
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
              </p>
              <Quantizations model={model} />
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
 * Which precisions a model is served at, and which of them a game will accept.
 *
 * On the card because it is part of the model's identity here, not a footnote: an id served at
 * fp4 is a different contestant from the same id at fp8, and a benchmark that hides which one it
 * measured is not saying much.
 */
function Quantizations({ model }: { model: ModelInfo }) {
  if (model.quantizations.length === 0) return null;

  const playable = new Set(model.playable_quantizations);
  const blocked = model.quantizations.filter((q) => !playable.has(q));

  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-1">
      {model.quantizations.map((quantization) => {
        const allowed = playable.has(quantization);
        return (
          <span
            key={quantization}
            title={
              allowed
                ? `served at ${quantization} — accepted`
                : `served at ${quantization} — excluded from games`
            }
            className={`border px-1 py-px font-mono text-[9px] uppercase tracking-wider ${
              allowed
                ? "border-good/40 text-good"
                : "border-bad-deep text-bad line-through decoration-bad/50"
            }`}
          >
            {quantization}
          </span>
        );
      })}
      {playable.size === 0 && (
        <span className="font-mono text-[9px] uppercase tracking-wider text-bad">
          unplayable
        </span>
      )}
      {blocked.length > 0 && playable.size > 0 && (
        <span className="font-mono text-[9px] text-ink-faint">
          {model.endpoint_count} endpoints
        </span>
      )}
    </p>
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

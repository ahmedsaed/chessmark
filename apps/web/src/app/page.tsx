import Link from "next/link";

import { HeroGame } from "@/components/HeroGame";
import { MiniBoard } from "@/components/MiniBoard";
import { NewGameSection } from "@/components/NewGameSection";
import { apiUrl, getGame, getLeaderboard, listEvents, listGames, listModels } from "@/lib/api";
import { pickReplays } from "@/lib/replays";
import type { GameDetail, GameSummary, LeaderboardRow, ModelInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [live, recent, models, board] = await Promise.all([
    listGames("running", 6),
    /* A wide window on purpose: the replay row draws from this pool, and a pool of twelve is
       mostly the same three games every load. */
    listGames(undefined, 60),
    listModels(),
    getLeaderboard(),
  ]);

  const finished = recent.filter((game) => game.status !== "running");

  /* The hero wants a running game; the most recent finished one keeps it from being empty
     between games, which is most of the time on a small deployment. */
  const featured = live[0] ?? finished[0] ?? null;
  const [game, events] = featured
    ? await Promise.all([getGame(featured.id), listEvents(featured.id)])
    : [null, []];

  /* Three clean finishes, reshuffled per request. The featured game is held out so the hero and
     the replay row cannot show the same game twice. */
  const picks = pickReplays(
    recent.filter((entry) => entry.id !== featured?.id),
    3,
  );
  const replays = (await Promise.all(picks.map((pick) => getGame(pick.id)))).filter(
    (detail): detail is GameDetail => detail !== null,
  );

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      {game ? (
        <HeroGame game={game} apiUrl={apiUrl} initialEvents={events} />
      ) : (
        <EmptyHero />
      )}

      {live.length > 1 && (
        <Strip title="Also live" count={live.length - 1}>
          {live.slice(1).map((entry) => (
            <GameCard key={entry.id} game={entry} />
          ))}
        </Strip>
      )}

      {replays.length > 0 && <Replays games={replays} />}

      <div className="mt-16 grid grid-cols-1 gap-10 lg:grid-cols-2">
        <TopContestants rows={board.rows} counted={board.games_counted} />
        <RecentGames games={finished.slice(0, 6)} />
      </div>

      <NewGameSection apiUrl={apiUrl} models={models} />

      <Contestants models={models} />
    </main>
  );
}

function EmptyHero() {
  return (
    <section className="border border-line bg-surface-2 px-6 py-16 text-center">
      <h1 className="font-serif text-4xl leading-tight text-ink">
        Language models play chess.
        <br />
        <span className="text-accent">Everything is recorded.</span>
      </h1>
      <p className="mx-auto mt-4 max-w-prose text-ink-dim">
        No games yet. Start one below, or run <code className="font-mono text-accent">make play</code>{" "}
        from the repo.
      </p>
    </section>
  );
}

function Strip({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-14">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          {title}
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-[10px] text-ink-faint">
          {count} game{count === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">{children}</ul>
    </section>
  );
}

/**
 * Three finished games, picked at random, shown by their final position.
 *
 * Only clean finishes — a checkmate or a resignation. A ply-cap draw or a budget stop is still
 * browsable from "Recent games", but it makes a poor replay: the interesting thing about those
 * records is why they stopped, not how they ended.
 */
function Replays({ games }: { games: GameDetail[] }) {
  return (
    <section className="mt-14">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Replays
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="font-mono text-[10px] text-ink-faint">scrub any of them ply by ply</span>
      </div>

      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {games.map((game) => {
          const white = game.players.find((p) => p.colour === "white");
          const black = game.players.find((p) => p.colour === "black");

          return (
            <li key={game.id}>
              <Link
                href={`/games/${game.id}`}
                /* `items-start` matters: the board wrapper is `flex-none` on the main axis only,
                   so cross-axis stretch was overriding `aspect-square` and rendering 104x122
                   boards. `h-full` makes every card fill its grid row so the three line up. */
                className="flex h-full items-start gap-4 border border-line bg-surface-2 p-3 transition-colors hover:border-accent-dim focus-visible:border-accent"
              >
                <div className="w-[104px] flex-none">
                  <MiniBoard
                    fen={game.current_fen}
                    label={`final position, ${white?.display_name ?? "white"} versus ${black?.display_name ?? "black"}`}
                  />
                </div>

                <div className="flex min-w-0 flex-col gap-1.5">
                  <p className="truncate font-mono text-[11px] text-ink">
                    {white?.display_name ?? "?"}
                  </p>
                  <p className="truncate font-mono text-[11px] text-ink">
                    {black?.display_name ?? "?"}
                  </p>
                  <p className="tabular font-mono text-[10px] text-accent">
                    {game.result}
                    <span className="ml-1.5 text-ink-faint">{game.termination}</span>
                  </p>
                  <p className="tabular font-mono text-[10px] text-ink-faint">
                    {game.ply_count} plies{game.is_ranked ? " · ranked" : ""}
                  </p>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * The top of the ranking, on the front page.
 *
 * The rating deviation travels with the rating everywhere it is shown. A visitor comparing a
 * contestant with one game against one with four needs to see that difference in the same glance,
 * or the ordering reads as more settled than it is.
 */
function TopContestants({ rows, counted }: { rows: LeaderboardRow[]; counted: number }) {
  return (
    <section>
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Top contestants
        </h2>
        <Link
          href="/leaderboard"
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
        >
          All →
        </Link>
      </div>

      {rows.length === 0 ? (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          No ranked games yet. Ratings only move on games played in the fixed ranked
          configuration — unranked games are recorded but never counted.
        </p>
      ) : (
        <>
          <ol className="flex flex-col gap-px border border-line-soft bg-line-soft">
            {rows.slice(0, 5).map((row, index) => (
              <li key={`${row.model_slug}@${row.quantization}`}>
                <Link
                  href={`/leaderboard/${encodeURIComponent(row.model_slug)}?q=${encodeURIComponent(row.quantization)}`}
                  className="flex items-center gap-3 bg-surface px-4 py-2.5 transition-colors hover:bg-surface-2"
                >
                  <span className="tabular w-4 flex-none font-mono text-[11px] text-ink-faint">
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
                    {row.model_slug}
                    <span className="text-ink-faint">@{row.quantization}</span>
                  </span>
                  <span className="tabular flex-none font-mono text-xs text-accent">
                    {Math.round(row.rating)}
                    <span className="ml-1 text-[10px] text-ink-faint">
                      ±{Math.round(row.rating_deviation)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
          <p className="tabular mt-2 font-mono text-[10px] text-ink-faint">
            Glicko-2 over {counted} ranked game{counted === 1 ? "" : "s"}
          </p>
        </>
      )}
    </section>
  );
}

function RecentGames({ games }: { games: GameSummary[] }) {
  return (
    <section>
      <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        Recent games
      </h2>
      {games.length === 0 ? (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          Nothing finished yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {games.map((game) => (
            <GameCard key={game.id} game={game} />
          ))}
        </ul>
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
function Contestants({ models }: { models: ModelInfo[] }) {
  const entrants = models.reduce((total, model) => total + model.contestants.length, 0);

  return (
    <section className="mt-16">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          The field
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-[10px] text-ink-faint">
          {models.length} models · {entrants} entrants
        </span>
      </div>

      <ul className="grid grid-cols-1 gap-px border border-line-soft bg-line-soft sm:grid-cols-2 lg:grid-cols-3">
        {models.slice(0, 24).map((model) => (
          <li key={model.id} className="bg-surface px-4 py-3">
            <p className="truncate font-mono text-xs text-ink">{model.openrouter_id}</p>
            <p className="tabular mt-1 font-mono text-[10px] text-ink-faint">
              {model.provider}
              {model.context_length ? ` · ${Math.round(model.context_length / 1000)}k ctx` : ""}
              {model.supports_reasoning ? " · reasoning" : ""}
              {model.is_floating_alias ? " · unrankable" : ""}
            </p>
            <ModelContestants model={model} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ModelContestants({ model }: { model: ModelInfo }) {
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

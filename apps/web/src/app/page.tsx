import { Suspense } from "react";
import Link from "next/link";

import { GameCard } from "@/components/GameCard";
import { HeroGame } from "@/components/HeroGame";
import { MyGames } from "@/components/MyGames";
import { ReplayBoard } from "@/components/ReplayBoard";
import { apiUrl, getGame, getLeaderboard, listGames } from "@/lib/api";
import { pickReplays } from "@/lib/replays";
import type { GameDetail, GameSummary, LeaderboardRow } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * The lobby.
 *
 * Every section fetches for itself behind its own `<Suspense>`, and that is the whole point.
 * The page used to `await` the lobby lists, then the featured game, then three replay details in
 * three sequential rounds, and render nothing until the slowest of them — the leaderboard —
 * came back. A visitor got a blank page for the length of the worst query on the page.
 *
 * Next.js memoises `fetch` for the duration of one request, so the sections asking for the same
 * list are not asking twice; they are reading the same in-flight promise. What that buys is
 * independence: the hero paints as soon as *it* is ready, and a slow ranking delays only the
 * ranking.
 */
export default function Home() {
  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <Suspense fallback={<HeroSkeleton />}>
        <Hero />
      </Suspense>

      {/* A game you are playing is not a game you are watching, and the lobby could not tell them
          apart. Renders nothing at all for a visitor with no games of their own. */}
      <MyGames heading="Your games" />

      <Suspense fallback={null}>
        <AlsoLive />
      </Suspense>

      <Suspense fallback={null}>
        <ReplayRow />
      </Suspense>

      <div className="mt-16 grid grid-cols-1 gap-10 lg:grid-cols-2">
        <Suspense fallback={<SectionSkeleton title="Top contestants" rows={5} />}>
          <TopContestants />
        </Suspense>
        <Suspense fallback={<SectionSkeleton title="Recent games" rows={4} />}>
          <RecentGames />
        </Suspense>
      </div>
    </main>
  );
}

/**
 * The pool the hero and the replay row both draw from.
 *
 * A wide window on purpose: a pool of twelve is mostly the same three games every load.
 */
function lobbyGames(): Promise<GameSummary[]> {
  return listGames(undefined, 60);
}

/**
 * Games that have stopped for good.
 *
 * `paused` is excluded alongside `running`: it is a live game waiting on a provider, and a paused
 * game listed among the finished ones would show a `*` where a result belongs.
 *
 * **A paused game appears nowhere on this page**, deliberately. It is excluded here so it cannot
 * print a `*` where a result belongs, and `listGames("running")` never returns one because the
 * status filter is exact — so the two exclusions together are the whole policy, and this comment
 * exists so neither is later "fixed" as an oversight. The front page is the first thing a visitor
 * sees and a board that has stopped is a poor introduction; a game waiting on somebody else's rate
 * limit is honest on its own page and on the lobby card, which is where a reader who wants it will
 * look.
 */
function settled(games: GameSummary[]): GameSummary[] {
  return games.filter((game) => game.status !== "running" && game.status !== "paused");
}

/**
 * The game the hero shows.
 *
 * Prefers a running game; falls back to the most recent finished one, which keeps the hero from
 * being empty between games — most of the time, on a small deployment.
 */
async function featuredGame(): Promise<GameSummary | null> {
  const [live, recent] = await Promise.all([listGames("running", 6), lobbyGames()]);
  return live[0] ?? settled(recent)[0] ?? null;
}

async function Hero() {
  const featured = await featuredGame();
  const game = featured ? await getGame(featured.id) : null;

  /* No event log. The hero shows a board and a move list, and `GameDetail.moves` is already the
     authoritative move list at the render's cursor — fetching the whole log to fold it back down
     to the same array cost 300KB of payload for a game of any length, on the one page every
     visitor loads first. A live game's *subsequent* moves still arrive on the stream. */
  return game ? <HeroGame game={game} apiUrl={apiUrl} /> : <EmptyHero />;
}

async function AlsoLive() {
  const live = await listGames("running", 6);
  if (live.length <= 1) return null;

  return (
    <Strip title="Also live" count={live.length - 1}>
      {live.slice(1).map((entry) => (
        <GameCard key={entry.id} game={entry} />
      ))}
    </Strip>
  );
}

async function ReplayRow() {
  const [recent, featured] = await Promise.all([lobbyGames(), featuredGame()]);

  /* The featured game is held out so the hero and the replay row cannot show the same game. */
  const picks = pickReplays(
    recent.filter((entry) => entry.id !== featured?.id),
    3,
  );
  const games = (await Promise.all(picks.map((pick) => getGame(pick.id)))).filter(
    (detail): detail is GameDetail => detail !== null,
  );
  if (games.length === 0) return null;

  return <Replays games={games} />;
}

async function TopContestants() {
  const board = await getLeaderboard();
  return <Contestants rows={board.rows} counted={board.games_counted} />;
}

async function RecentGames() {
  const games = settled(await lobbyGames()).slice(0, 6);

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
 * Placeholders that hold the shape they will be replaced by.
 *
 * Sized to the real thing on purpose: a fallback that is a different height moves the page under
 * the reader's cursor when it resolves, which reads worse than the wait it was hiding.
 */
function HeroSkeleton() {
  return (
    <section className="grid grid-cols-1 items-center gap-8 lg:grid-cols-[minmax(0,440px)_minmax(0,1fr)] lg:gap-12">
      <div className="mx-auto aspect-square w-full max-w-[440px] animate-pulse bg-surface-2" />
      <div className="flex min-w-0 flex-col gap-5">
        <h1 className="font-serif text-4xl leading-[1.1] text-ink sm:text-5xl">
          Language models play chess.
          <br />
          <span className="text-accent">Everything is recorded.</span>
        </h1>
        <div className="h-24 animate-pulse bg-surface-2" />
      </div>
    </section>
  );
}

function SectionSkeleton({ title, rows }: { title: string; rows: number }) {
  return (
    <section>
      <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        {title}
      </h2>
      <div className="flex flex-col gap-3">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="h-14 animate-pulse bg-surface-2" />
        ))}
      </div>
    </section>
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
 * Three finished games, picked at random, each playing itself.
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
        <span className="font-mono text-[10px] text-ink-faint">playing · open one to scrub it</span>
      </div>

      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {games.map((game, index) => {
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
                <div className="w-[120px] flex-none">
                  <ReplayBoard
                    startFen={game.start_fen}
                    moves={game.moves}
                    index={index}
                    count={games.length}
                    label={`${white?.display_name ?? "white"} versus ${black?.display_name ?? "black"}`}
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
function Contestants({ rows, counted }: { rows: LeaderboardRow[]; counted: number }) {
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
                  href={`/models/${row.model_slug}#c-${encodeURIComponent(row.quantization)}`}
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

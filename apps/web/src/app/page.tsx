import type { Metadata } from "next";
import Link from "next/link";

import { BeforeYouAsk } from "@/components/BeforeYouAsk";
import { ChallengeSection } from "@/components/ChallengeSection";
import { GameCard } from "@/components/GameCard";
import { HeroGame } from "@/components/HeroGame";
import { ReplayBoard } from "@/components/ReplayBoard";
import { TopContestants } from "@/components/TopContestants";
import { TurnSpotlight } from "@/components/TurnSpotlight";
import { TournamentsSection } from "@/components/TournamentsSection";
import {
  apiUrl,
  getGame,
  getHumanRecord,
  getLeaderboard,
  listGames,
  listTournaments,
  openingEvents,
} from "@/lib/api";
import { pickReplays } from "@/lib/replays";
import { pickTurn } from "@/lib/spotlight";
import { foldEvents } from "@/lib/turns";
import type { GameDetail, GameEvent, GameSummary, TurnView } from "@/lib/types";

/* Title and description are the root layout's, which are already this page's — the lobby is the
   site. Only the canonical is stated, and only here: on the layout it would be inherited by every
   route and declare the whole site a duplicate of `/`. */
export const metadata: Metadata = { alternates: { canonical: "/" } };

/**
 * The lobby.
 *
 * **The same page for everybody, deliberately.** It carried a "Your games" section and read the
 * session cookie to decide whether to draw it, which made the first page every visitor loads a
 * *personalised* one — and the only page on the site that could not be reasoned about without
 * knowing who was asking. `/profile` is where a person's own games live now, and it is a better
 * home for them than a strip above the leaderboard.
 *
 * Nothing here reads a cookie, a header, or anything else about the request. That is worth keeping:
 * it is what lets this page be cached per *content* rather than per reader, and a personalised
 * fragment on a cached page is how one visitor gets served another's games.
 */
export default async function Home() {
  /**
   * **One render, two rounds, nothing streamed.**
   *
   * This page used to put every section behind its own `<Suspense>`, so each painted when it was
   * ready and the lobby assembled itself in front of the reader. That was the right answer while
   * each section cost a live API round trip and the slowest of them — the ranking — held the page.
   * It is the wrong answer now the reads are cached and tagged (ADR-0046): the whole page renders
   * in about the time the shell alone used to take, so the boundaries bought nothing and cost a
   * visible assembly. Measured on this machine: first byte 4.4ms and complete at 43.1ms became
   * first byte 12.9ms and complete at 13.1ms.
   *
   * The fetching is still parallel, which is what the boundaries were really providing. Two rounds
   * rather than one because the hero and the replay row both depend on *which* game is featured,
   * and that is not known until the lists come back. Next.js memoises `fetch` per request, so the
   * two calls for the lobby list below are one request.
   */
  const [live, recent, board, tournaments, humans] = await Promise.all([
    listGames("running", 6),
    lobbyGames(),
    getLeaderboard(),
    /* One cached list, tagged `tournaments` (ADR-0046). The section shows three of them and asks
       for nothing else — no standings, no per-event detail. */
    listTournaments(),
    /* Four integers, one aggregate, tagged `games`. The "know your opponent" half of that section
       is summed from `board` above and costs nothing. */
    getHumanRecord(),
  ]);

  /* Prefers a running game; falls back to the most recent finished one, which keeps the hero from
     being empty between games — most of the time, on a small deployment. */
  const featured = live[0] ?? settled(recent)[0] ?? null;

  /* The featured game is held out so the hero and the replay row cannot show the same game. */
  /* Six, not three: the grid is three wide at `lg`, so three filled one row and left the section
     looking like the top of something cut off. Six is two full rows there, three at `sm`, and a
     column on a phone. */
  const picks = pickReplays(
    recent.filter((entry) => entry.id !== featured?.id),
    6,
  );

  /**
   * The two games the turn excerpt may come from.
   *
   * **Sorted by illegal attempts, because that is the turn worth showing**: a model proposing a
   * move the referee refuses, and then finding a legal one, is the benchmark happening in front of
   * the reader. Two rather than one because plenty of models publish no reasoning text at all
   * (`TurnView.reasoning`: "DeepSeek fills this; Gemini never does"), and a section that vanishes
   * on half of the archive is worse than a second 14KB read.
   */
  const candidates = [...settled(recent)]
    .sort((a, b) => illegalIn(b) - illegalIn(a))
    .slice(0, SPOTLIGHT_GAMES);

  const [heroGame, ...rest] = await Promise.all([
    /* No event log. The hero shows a board and a move list, and `GameDetail.moves` is already the
       authoritative move list at the render's cursor — fetching the whole log to fold it back down
       to the same array cost 300KB of payload for a game of any length, on the one page every
       visitor loads first. A live game's *subsequent* moves still arrive on the stream. */
    featured ? getGame(featured.id, { settled: featured.status === "finished" }) : Promise.resolve(null),
    /* `pickReplays` returns only `status === "finished"` games, so these can never move again. */
    ...picks.map((pick) => getGame(pick.id, { settled: true })),
    /* The opening of each candidate, bounded and cached — not the whole log. */
    ...candidates.map((game) => openingEvents(game.id)),
  ]);

  const replayDetails = rest.slice(0, picks.length) as (GameDetail | null)[];
  const openings = rest.slice(picks.length) as GameEvent[][];
  const replays = replayDetails.filter((detail): detail is GameDetail => detail !== null);
  const spotlight = firstTurnWorthShowing(candidates, openings);
  const alsoLive = live.slice(1);
  const recentGames = settled(recent).slice(0, 6);

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      {heroGame ? <HeroGame game={heroGame} apiUrl={apiUrl} /> : <EmptyHero />}

      {alsoLive.length > 0 && (
        <Strip title="Also live" count={alsoLive.length}>
          {alsoLive.map((entry) => (
            <GameCard key={entry.id} game={entry} />
          ))}
        </Strip>
      )}

      {replays.length > 0 && <Replays games={replays} />}

      {spotlight && <TurnSpotlight game={spotlight.game} turn={spotlight.turn} />}

      <TopContestants rows={board.rows} counted={board.games_counted} />

      {/* After the ranking, because it is the machinery behind it: the podium says who is ahead,
          this says what they are playing in. */}
      <TournamentsSection tournaments={tournaments} />

      {/* After the tournaments: the models have been introduced and ranked, and this is the reply
          to "could I beat one of those". */}
      <ChallengeSection
        record={humans}
        rows={board.rows}
        gamesCounted={board.games_counted}
      />

      <RecentGames games={recentGames} />

      {/* Last, because it is the page's closing argument rather than part of its pitch: by here a
          reader has seen the ranking, the events and the record, and the question left is whether
          to believe any of it. */}
      <BeforeYouAsk />
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
 * The last six games that stopped for good.
 *
 * Full width, three across, below the ranking. It shared a row with the top five contestants when
 * both were lists; the leaderboard is a podium now and wants the whole page, and six cards in one
 * column beside it would have been a very tall, very thin strip of nothing.
 */
function RecentGames({ games }: { games: GameSummary[] }) {
  return (
    <section className="mt-14">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Recent games
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-meta text-ink-faint">
          {games.length} game{games.length === 1 ? "" : "s"}
        </span>
      </div>
      {games.length === 0 ? (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          Nothing finished yet.
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {games.map((game) => (
            <GameCard key={game.id} game={game} />
          ))}
        </ul>
      )}
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
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          {title}
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="tabular font-mono text-meta text-ink-faint">
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
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Replays
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <span className="font-mono text-meta text-ink-faint">playing · open one to scrub it</span>
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
                  <p className="truncate font-mono text-data text-ink">
                    {white?.display_name ?? "?"}
                  </p>
                  <p className="truncate font-mono text-data text-ink">
                    {black?.display_name ?? "?"}
                  </p>
                  <p className="tabular font-mono text-meta text-accent">
                    {game.result}
                    <span className="ml-1.5 text-ink-faint">{game.termination}</span>
                  </p>
                  <p className="tabular font-mono text-meta text-ink-faint">
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

/** How many finished games the lobby may read an opening from. */
const SPOTLIGHT_GAMES = 2;

function illegalIn(game: GameSummary): number {
  return game.players.reduce((total, player) => total + player.illegal_attempts, 0);
}

/**
 * The first candidate whose opening has a turn worth showing.
 *
 * Order matters and is not "best across both": the games are already sorted by how much went wrong
 * in them, so the first one that *has* something to show is the one to show. Falling through to the
 * second is for the case where the first published no thinking at all, which is a property of the
 * model rather than of the game.
 */
function firstTurnWorthShowing(
  games: GameSummary[],
  openings: GameEvent[][],
): { game: GameSummary; turn: TurnView } | null {
  for (const [index, game] of games.entries()) {
    const events = openings[index] ?? [];
    if (events.length === 0) continue;

    const turn = pickTurn(foldEvents(events, []).turns);
    if (turn) return { game, turn };
  }
  return null;
}

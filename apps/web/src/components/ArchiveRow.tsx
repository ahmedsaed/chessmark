/**
 * One game in the archive, as a row (UI-12).
 *
 * A row rather than `GameCard`, because the card is built for a lobby of six and the archive shows
 * fifty: a grid of cards is a page of scrolling to read what a column of rows says at a glance. The
 * facts are the same ones the card carries — the two seats, how it stands, how it ended, its length
 * — plus the two a person searching an archive sorts by, cost and date.
 *
 * **Names wrap; they are never truncated** (UI-11). A model's name is the one thing on the row a
 * reader is looking for, and production's are far longer than the seed's.
 */

import Link from "next/link";

import { endingLabel } from "@/lib/archive";
import type { GameSummary, Player } from "@/lib/types";

/** The date in UTC, spelled the same on every server whatever its locale. */
const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount === 0) return "—";
  return amount < 0.01 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`;
}

export function ArchiveRow({ game }: { game: GameSummary }) {
  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");

  return (
    <li>
      <Link
        href={`/games/${game.id}`}
        className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 bg-surface px-4 py-3 transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 sm:grid-cols-[minmax(0,1fr)_10rem_4.5rem_5rem_6.5rem]"
      >
        <span className="col-span-2 flex min-w-0 flex-col gap-0.5 sm:col-span-1">
          <Seat player={white} won={game.winner_colour === "white"} colour="white" />
          <Seat player={black} won={game.winner_colour === "black"} colour="black" />
        </span>

        <Standing game={game} />

        <span className="tabular hidden font-mono text-meta text-ink-faint sm:block sm:text-right">
          {game.ply_count} plies
        </span>
        <span className="tabular hidden font-mono text-meta text-ink-faint sm:block sm:text-right">
          {usd(game.total_cost_usd)}
        </span>
        {/* On a phone the three numbers share one line under the names; from `sm` each has a
            column of its own and this collapses to the date alone. */}
        <span className="tabular text-right font-mono text-meta text-ink-faint">
          <span className="sm:hidden">{game.ply_count} plies · </span>
          <time dateTime={game.created_at}>{DATE.format(new Date(game.created_at))}</time>
        </span>
      </Link>
    </li>
  );
}

function Seat({
  player,
  won,
  colour,
}: {
  player: Player | undefined;
  won: boolean;
  colour: "white" | "black";
}) {
  return (
    <span className="flex min-w-0 items-baseline gap-2">
      <i
        aria-hidden
        className={`block h-2 w-2 flex-none translate-y-[-1px] border border-line ${
          colour === "white" ? "bg-piece-white" : "bg-piece-black"
        }`}
      />
      <span className="sr-only">{colour}: </span>
      <span
        className={`min-w-0 break-words font-mono text-xs ${won ? "text-ink" : "text-ink-dim"}`}
      >
        {player?.display_name ?? "?"}
        {player?.kind === "human" && (
          /* A glyph rather than the word: it marks the seat without competing with the name. The
             word is still there for a screen reader, and on hover. */
          <span title="a person" className="ml-1.5 inline-block align-[-1px] text-ink-faint">
            <svg aria-hidden viewBox="0 0 12 12" className="h-3 w-3">
              <circle cx="6" cy="3.5" r="2.25" fill="currentColor" />
              <path d="M1.5 11.5c0-2.6 2-4.5 4.5-4.5s4.5 1.9 4.5 4.5z" fill="currentColor" />
            </svg>
            <span className="sr-only"> (a person)</span>
          </span>
        )}
        {won && <span className="sr-only"> (won)</span>}
      </span>
    </span>
  );
}

/** How the game stands: live, paused or aborted, or its result and how it was reached. */
function Standing({ game }: { game: GameSummary }) {
  const tag = "font-mono text-label uppercase tracking-[0.14em]";

  let status;
  if (game.status === "running") {
    status = (
      <span className={`inline-flex items-center gap-1.5 ${tag} text-bad`}>
        <i aria-hidden className="block h-1.5 w-1.5 animate-pulse rounded-full bg-bad" />
        live
      </span>
    );
  } else if (game.status === "paused") {
    status = (
      <span className={`${tag} text-ink-faint`} title={game.pause_reason ?? undefined}>
        paused
      </span>
    );
  } else if (game.status === "aborted" || game.status === "pending") {
    status = <span className={`${tag} text-ink-faint`}>{game.status}</span>;
  } else {
    status = <span className="tabular font-mono text-data text-accent">{game.result}</span>;
  }

  return (
    <span className="flex flex-col items-start gap-0.5">
      {status}
      {game.termination && (
        <span className="font-mono text-label text-ink-faint">
          {endingLabel(game.termination)}
          {game.is_ranked ? " · ranked" : ""}
        </span>
      )}
      {!game.termination && game.is_ranked && (
        <span className="font-mono text-label text-ink-faint">ranked</span>
      )}
    </span>
  );
}

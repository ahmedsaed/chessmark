/**
 * One game, as a link.
 *
 * Lived inside the landing page until Phase 10 needed the same card for "your games". Two copies
 * of a card that is meant to read identically wherever a game is listed is exactly the drift
 * worth avoiding, so it moved here rather than being pasted.
 *
 * A server component: it renders in the lobby during server rendering and inside the client
 * component that lists your own games, and it holds no state either way.
 */

import Link from "next/link";

import type { Colour, GameSummary } from "@/lib/types";

export function GameCard({
  game,
  seat,
  yourTurn = false,
}: {
  game: GameSummary;
  /** The colour *you* hold here, when this is a game you are playing. */
  seat?: Colour;
  /** Running and waiting on you. Drawn as a badge, because it is the only reason to hurry. */
  yourTurn?: boolean;
}) {
  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");
  const running = game.status === "running";
  const illegal = game.players.reduce((total, p) => total + p.illegal_attempts, 0);

  return (
    <li>
      <Link
        href={`/games/${game.id}`}
        className={`flex flex-col gap-2.5 border bg-surface-2 p-4 transition-colors focus-visible:border-accent ${
          yourTurn ? "border-accent-dim hover:border-accent" : "border-line hover:border-accent-dim"
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <span className="truncate font-mono text-xs text-ink">
            {white?.display_name ?? "?"} <span className="text-ink-faint">vs</span>{" "}
            {black?.display_name ?? "?"}
          </span>

          {yourTurn ? (
            <span className="flex-none border border-accent-deep bg-accent px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.14em] text-on-accent">
              your move
            </span>
          ) : running ? (
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
          {seat ? `you play ${seat} · ` : ""}
          {game.ply_count} plies
          {game.termination ? ` · ${game.termination}` : ""}
          {illegal > 0 ? ` · ${illegal} illegal` : ""}
          {game.is_ranked ? " · ranked" : ""}
        </p>
      </Link>
    </li>
  );
}

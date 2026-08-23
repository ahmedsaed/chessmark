"use client";

/**
 * The landing hero: an actual game, playing.
 *
 * The old hero was a headline over a stack of lists — a chess site whose front page had no
 * chessboard on it. This puts the product first: a visitor sees two models playing within a
 * second, before reading anything.
 *
 * Prefers a running game; falls back to the most recent finished one so the hero is never empty
 * between games. The position is replayed locally through chess.js from `move_made` events, the
 * same way `LiveGame` does it — the server is the authority on legality, and replaying SAN means
 * a duplicated or out-of-order event cannot desync the board.
 */

import Link from "next/link";
import { useMemo } from "react";
import { Chess } from "chess.js";

import { Board } from "@/components/Board";
import { useGameStream } from "@/hooks/useGameStream";
import { tailMoves } from "@/lib/moves";
import { foldEvents } from "@/lib/turns";
import type { GameDetail, GameEvent } from "@/lib/types";

const TERMINAL = new Set(["finished", "aborted"]);

export function HeroGame({
  game,
  apiUrl,
  initialEvents,
}: {
  game: GameDetail;
  apiUrl: string;
  initialEvents: GameEvent[];
}) {
  const isLive = !TERMINAL.has(game.status);

  const { events } = useGameStream({
    gameId: game.id,
    apiUrl,
    afterSeq: game.event_seq,
    enabled: isLive,
  });

  const { moves, ended } = useMemo(
    () => foldEvents([...initialEvents, ...events], []),
    [initialEvents, events],
  );

  const { fen, lastMove, toMove } = useMemo(() => {
    const board = new Chess(game.start_fen);
    let last: { from: string; to: string } | null = null;

    for (const san of moves) {
      try {
        const move = board.move(san);
        last = { from: move.from, to: move.to };
      } catch {
        break;
      }
    }

    return {
      fen: board.fen(),
      lastMove: last,
      toMove: board.isGameOver() ? null : board.turn() === "w" ? "white" : "black",
    } as const;
  }, [game.start_fen, moves]);

  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");
  const running = isLive && ended === null;

  /* `chess.js` only knows about mate and stalemate, so after a resignation, a forfeit, or the ply
     cap the position is still "playable" and `toMove` resolves to a colour. Showing "thinking…"
     under a finished game's result is a small lie about a live model. The game's own status is
     the authority here, not the board. */
  const waitingOn = running ? toMove : null;

  return (
    <section className="grid grid-cols-1 items-center gap-8 lg:grid-cols-[minmax(0,440px)_minmax(0,1fr)] lg:gap-12">
      <div className="mx-auto w-full max-w-[440px]">
        <Board fen={fen} lastMove={lastMove} />
      </div>

      <div className="flex min-w-0 flex-col gap-5">
        <h1 className="font-serif text-4xl leading-[1.1] text-ink sm:text-5xl">
          Language models play chess.
          <br />
          <span className="text-accent">Everything is recorded.</span>
        </h1>

        <p className="max-w-prose text-ink-dim">
          Agents move through tools, carry one transcript across the whole game, and are judged on
          whether they can hold a board in their head for eighty moves. Every request, reasoning
          trace, tool call, and taunt is stored and replayable.
        </p>

        <div className="border border-line bg-surface-2">
          <div className="flex items-center justify-between gap-3 border-b border-line-soft px-4 py-2.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
              {running ? "Live now" : "Most recent"}
            </span>
            {running ? (
              <span className="inline-flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.14em] text-bad">
                <i aria-hidden className="block h-1.5 w-1.5 animate-pulse rounded-full bg-bad" />
                ply {moves.length}
              </span>
            ) : (
              <span className="tabular font-mono text-[11px] text-accent">{game.result}</span>
            )}
          </div>

          <div className="flex flex-col gap-2 px-4 py-3">
            <Seat name={black?.display_name ?? "?"} colour="black" toMove={waitingOn === "black"} />
            <Seat name={white?.display_name ?? "?"} colour="white" toMove={waitingOn === "white"} />
          </div>

          {moves.length > 0 && (
            <p className="tabular border-t border-line-soft px-4 py-2.5 font-mono text-[11px] leading-relaxed text-ink-dim">
              {tailMoves(moves)}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Link
            href={`/games/${game.id}`}
            className="border border-accent-deep bg-accent px-4 py-2 font-mono text-[11px] uppercase tracking-[0.14em] text-on-accent transition-colors hover:bg-accent-dim"
          >
            {running ? "Watch live →" : "Replay it →"}
          </Link>
          <Link
            href="/leaderboard"
            className="border border-line bg-surface px-4 py-2 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-dim transition-colors hover:border-accent-dim hover:text-ink"
          >
            Leaderboard
          </Link>
        </div>
      </div>
    </section>
  );
}

function Seat({
  name,
  colour,
  toMove,
}: {
  name: string;
  colour: "white" | "black";
  toMove: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <i
        aria-hidden
        className={`block h-2.5 w-2.5 flex-none border ${
          colour === "white" ? "border-line bg-piece-white" : "border-ink-faint bg-piece-black"
        }`}
      />
      <span className="truncate font-mono text-xs text-ink">{name}</span>
      {toMove && (
        <span className="flex-none font-mono text-[9px] uppercase tracking-[0.14em] text-machine">
          thinking…
        </span>
      )}
    </div>
  );
}

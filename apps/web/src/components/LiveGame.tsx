"use client";

/**
 * The live game view: stats left, board centre, conversation right (ADR-0013).
 *
 * The two side columns are the same width by construction and the board takes everything left
 * over, so it is as large as the layout allows.
 *
 * The server renders the game up to `event_seq`; this subscribes from that cursor and folds
 * arriving events on top. The position is derived by replaying SAN through chess.js rather than
 * trusting a FEN from the wire — the server is the authority on legality, and replaying locally
 * means an out-of-order or duplicated event cannot desync the board.
 */

import { useMemo } from "react";
import { Chess } from "chess.js";

import { Board } from "@/components/Board";
import { Conversation } from "@/components/Conversation";
import { StatsRail } from "@/components/StatsRail";
import { useGameStream } from "@/hooks/useGameStream";
import { foldEvents } from "@/lib/turns";
import type { GameDetail, GameEvent } from "@/lib/types";

const TERMINAL = new Set(["finished", "aborted"]);

export function LiveGame({
  game,
  apiUrl,
  initialEvents,
}: {
  game: GameDetail;
  apiUrl: string;
  initialEvents: GameEvent[];
}) {
  const { events, status } = useGameStream({
    gameId: game.id,
    apiUrl,
    afterSeq: game.event_seq,
    enabled: !TERMINAL.has(game.status),
  });

  // History first, then whatever has streamed in since. The move list is rebuilt from
  // `move_made` events rather than from `game.moves`, so there is exactly one source of truth and
  // no chance of counting a move twice.
  const { turns, moves, ended } = useMemo(
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
        // A move we cannot replay means our view has drifted from the server's. Stop here rather
        // than render a position that never existed; the next page load re-syncs from Postgres.
        break;
      }
    }

    return {
      fen: board.fen(),
      lastMove: last,
      toMove: board.isGameOver() ? null : (board.turn() === "w" ? "white" : "black"),
    } as const;
  }, [moves, game.start_fen]);

  const outcome = ended ?? terminalFrom(game);

  return (
    <div className="flex flex-col gap-4">
      <Header game={game} status={status} outcome={outcome} />

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[var(--rail)_minmax(0,1fr)_var(--rail)] [--rail:262px]">
        {/* Stacked on a phone the order is board, conversation, stats: the conversation is the
            reason someone opened the page, and burying it under the full stats rail means
            scrolling past telemetry to reach the fight (ADR-0013). */}
        <div className="order-3 lg:order-none">
          <StatsRail game={game} toMove={toMove} moves={moves} />
        </div>

        {/* The board is square, so capping its width caps its height — otherwise on a short
            viewport it grows past the fold and the panels beside it are pushed off-screen. */}
        <div className="order-1 mx-auto flex w-full max-w-[min(100%,calc(100vh-13rem))] flex-col gap-2.5 lg:order-none">
          <div className="flex items-baseline justify-between font-mono text-[11px] uppercase tracking-[0.08em] text-ink-faint">
            <span>
              {game.players.find((p) => p.colour === "white")?.display_name} —{" "}
              {game.players.find((p) => p.colour === "black")?.display_name}
            </span>
            <span className="text-accent">
              {outcome ? outcome.result : toMove ? `${toMove} to move` : ""}
            </span>
          </div>
          <Board fen={fen} lastMove={lastMove} />
        </div>

        <div className="order-2 flex max-h-[70vh] min-h-[320px] flex-col lg:order-none lg:max-h-[calc(100vh-14rem)]">
          <Conversation turns={turns} />
        </div>
      </div>
    </div>
  );
}

function terminalFrom(game: GameDetail) {
  if (!TERMINAL.has(game.status)) return null;
  return {
    result: game.result,
    termination: game.termination ?? "",
    detail: game.termination_detail ?? "",
  };
}

function Header({
  game,
  status,
  outcome,
}: {
  game: GameDetail;
  status: string;
  outcome: { result: string; termination: string; detail: string } | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {outcome ? (
        <span className="border border-good px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-good">
          {outcome.result} · {outcome.termination}
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-bad">
          <i
            aria-hidden
            className={`block h-1.5 w-1.5 rounded-full bg-bad ${
              status === "live" ? "animate-pulse" : ""
            }`}
          />
          {status === "reconnecting" ? "reconnecting" : "live"}
        </span>
      )}
      <span className="font-mono text-[10px] text-ink-faint">game {game.id.slice(0, 8)}</span>
      {outcome?.detail && (
        <span className="text-xs text-ink-dim">{outcome.detail}</span>
      )}
    </div>
  );
}

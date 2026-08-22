"use client";

/**
 * The replay view: a finished game, scrubbable ply by ply.
 *
 * It is the live view with a transport bolted on, and that is deliberate. The position, the
 * conversation, and the move list all come from `foldEvents` over a truncated event log
 * (`lib/replay.ts`), so replay cannot drift from what a spectator actually saw — there is one
 * implementation of "what the game looked like", not two (ADR-0008).
 *
 * The board is still derived by replaying SAN through chess.js rather than by trusting a FEN from
 * the wire, for the same reason the live view does it: the server is the authority on legality,
 * and a locally-derived position cannot be desynced by a malformed payload.
 */

import { useMemo, useState } from "react";
import { Chess } from "chess.js";

import { Board } from "@/components/Board";
import { Conversation } from "@/components/Conversation";
import { RawTranscript } from "@/components/RawTranscript";
import { Scrubber } from "@/components/Scrubber";
import { StatsRail } from "@/components/StatsRail";
import { eventsThroughPly, plyCount, turnIdsByPly } from "@/lib/replay";
import { foldEvents } from "@/lib/turns";
import type { GameDetail, GameEvent, TurnSummary, TurnView } from "@/lib/types";

export function Replay({
  game,
  apiUrl,
  events,
  turns: turnRows,
}: {
  game: GameDetail;
  apiUrl: string;
  events: GameEvent[];
  turns: TurnSummary[];
}) {
  const total = useMemo(() => plyCount(events), [events]);

  // Opens at the final position: someone following a shared link wants the result first, and the
  // scrubber is right there to wind back.
  const [ply, setPly] = useState(total);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [inspecting, setInspecting] = useState<TurnView | null>(null);

  const { turns, moves, ended } = useMemo(
    () => foldEvents(eventsThroughPly(events, ply), []),
    [events, ply],
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
  }, [moves, game.start_fen]);

  const turnIds = useMemo(() => turnIdsByPly(turnRows), [turnRows]);
  const focus = turns.at(-1) ?? null;

  // At the end of a finished game nobody is to move, whatever chess.js thinks. Most of our games
  // end by forfeit, budget, or the ply cap — endings the rules of chess know nothing about, so
  // the position is often still legally playable and the badge would invite a move that will
  // never come. Mid-scrub it is real information and stays.
  const atEnd = ply >= total;
  const sideToMove = atEnd ? null : toMove;

  function startPlaying(next: boolean) {
    // Pressing play at the end rewinds rather than doing nothing — the alternative is a button
    // that looks enabled and is not.
    if (next && ply >= total) setPly(0);
    setPlaying(next);
  }

  return (
    <div className="flex flex-col gap-4">
      <Header game={game} ended={ended} ply={ply} total={total} />

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[var(--rail)_minmax(0,1fr)_var(--rail)] [--rail:262px]">
        <div className="order-3 lg:order-none">
          <StatsRail
            game={game}
            toMove={sideToMove}
            moves={moves}
            activePly={ply}
            onSeek={(next) => {
              setPlaying(false);
              setPly(next);
            }}
          />
        </div>

        <div className="order-1 mx-auto flex w-full max-w-[min(100%,calc(100vh-16rem))] flex-col gap-2.5 lg:order-none">
          <div className="flex items-baseline justify-between font-mono text-[11px] uppercase tracking-[0.08em] text-ink-faint">
            <span>
              {game.players.find((p) => p.colour === "white")?.display_name} —{" "}
              {game.players.find((p) => p.colour === "black")?.display_name}
            </span>
            <span className="text-accent">{game.result}</span>
          </div>
          <Board fen={fen} lastMove={lastMove} />
          <Scrubber
            ply={ply}
            total={total}
            playing={playing}
            speed={speed}
            onSeek={setPly}
            onPlayingChange={startPlaying}
            onSpeedChange={setSpeed}
            keysEnabled={inspecting === null}
          />
        </div>

        <div className="order-2 flex max-h-[70vh] min-h-[320px] flex-col lg:order-none lg:max-h-[calc(100vh-14rem)]">
          <Conversation
            turns={turns}
            emptyMessage="The starting position — step forward to begin."
            focusKey={focus?.key ?? null}
            onInspect={(turn) => turnIds.has(turn.ply) && setInspecting(turn)}
          />
        </div>
      </div>

      {inspecting && turnIds.has(inspecting.ply) && (
        <RawTranscript
          apiUrl={apiUrl}
          gameId={game.id}
          turnId={turnIds.get(inspecting.ply)!}
          label={`ply ${inspecting.ply} · ${inspecting.model || inspecting.colour}`}
          onClose={() => setInspecting(null)}
        />
      )}
    </div>
  );
}

function Header({
  game,
  ended,
  ply,
  total,
}: {
  game: GameDetail;
  ended: { result: string; termination: string; detail: string } | null;
  ply: number;
  total: number;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="border border-good px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-good">
        {game.result} · {game.termination ?? "—"}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-machine">
        replay
      </span>
      <span className="font-mono text-[10px] text-ink-faint">game {game.id.slice(0, 8)}</span>
      {/* The detail is the ending's explanation, so it appears only once the ending is on screen. */}
      {ended?.detail && ply >= total && (
        <span className="text-xs text-ink-dim">{ended.detail}</span>
      )}
    </div>
  );
}

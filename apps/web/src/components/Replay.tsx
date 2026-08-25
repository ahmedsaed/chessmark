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
import { captures } from "@/lib/captures";
import { RawTranscript } from "@/components/RawTranscript";
import { Scrubber } from "@/components/Scrubber";
import { PlayerBar } from "@/components/PlayerBar";
import { StatsRail } from "@/components/StatsRail";
import { eventsThroughPly, plyCount, turnIdsByPly } from "@/lib/replay";
import { foldEvents } from "@/lib/turns";
import type { GameDetail, GameEvent, TurnSummary, TurnView } from "@/lib/types";

export function Replay({
  game,
  apiUrl,
  events,
  turns: turnRows,
  actions,
}: {
  game: GameDetail;
  apiUrl: string;
  events: GameEvent[];
  turns: TurnSummary[];
  /** Copy-link and PGN. They ride the status row rather than a bar of their own (UI feedback). */
  actions?: React.ReactNode;
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

  /* Captures at the ply being shown, not at the end — scrubbing back should show the material as
     it stood then, which is half of what makes a replay worth scrubbing. */
  const { taken, advantage } = useMemo(() => {
    const { white, black, advantage: lead } = captures(fen);
    return { taken: { white, black }, advantage: lead };
  }, [fen]);

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
      <Header game={game} ended={ended} ply={ply} total={total} actions={actions} />

      {/* One expression does the whole layout.
          The board is square, so its size is bounded by whichever runs out first — the height left
          under the page chrome, or a reasonable share of the width. `min()` of those two is the
          board column; the two rails are `1fr` each and split everything else, so there is never a
          dead gutter and never a horizontal scrollbar.
          Deriving the board's width from its own height instead is the obvious idea and does not
          work: in a grid an `auto` column must resolve its width before the row height is known,
          and in flex the same knot ties itself the other way. Both were tried. */}
      <div className="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-9.5rem)] lg:grid-cols-[minmax(0,1fr)_min(calc(100dvh-12rem),52vw)_minmax(0,1fr)]">
        <div className="order-3 min-w-0 overflow-y-auto lg:order-none">
          <StatsRail game={game} toMove={sideToMove} activePly={ply} />
        </div>

        <div className="order-1 flex min-h-0 min-w-0 flex-col gap-2 lg:order-none">
          {/* Black above, White below — each player on the side their pieces are on, and the
              captures beside the name, so scrubbing shows material swing as it happened. */}
          <PlayerBar
            player={game.players.find((p) => p.colour === "black")}
            taken={taken.black}
            advantage={Math.max(0, -advantage)}
            active={sideToMove === "black"}
            toMoveLabel={game.result}
          />
          <Board fen={fen} lastMove={lastMove} />
          <PlayerBar
            player={game.players.find((p) => p.colour === "white")}
            taken={taken.white}
            advantage={Math.max(0, advantage)}
            active={sideToMove === "white"}
          />
        </div>

        <div className="order-2 flex min-h-[24rem] min-w-0 flex-col lg:order-none lg:min-h-0">
          <Conversation
            turns={turns}
            players={game.players}
            emptyMessage="The starting position — step forward to begin."
            focusKey={focus?.key ?? null}
            onInspect={(turn) => turnIds.has(turn.ply) && setInspecting(turn)}
            header={
              /* The transport sits with the conversation rather than under the board: it is what
                 scrubs both, and taking it out of the centre column gives the board back the
                 height that is the only thing limiting how large it can be. */
              <div className="flex-none border-b border-line p-2">
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
            }
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
  actions,
}: {
  game: GameDetail;
  ended: { result: string; termination: string; detail: string } | null;
  ply: number;
  total: number;
  actions?: React.ReactNode;
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
      {/* Wrapped rather than rendered bare. `actions` is built in a Server Component and crosses
          the RSC boundary into this client one, which lands it in the children array without a
          key — React warns. A wrapper gives it a single-child slot instead of a list position. */}
      {actions && <span className="ml-auto flex items-center">{actions}</span>}
    </div>
  );
}

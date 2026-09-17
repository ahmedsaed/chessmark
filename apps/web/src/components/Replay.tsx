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

import { useCallback, useMemo, useState } from "react";

import { Board } from "@/components/Board";
import { EventStream } from "@/components/EventStream";
import { GameLayout } from "@/components/GameLayout";
import { captures } from "@/lib/captures";
import { RawTranscript } from "@/components/RawTranscript";
import { Scrubber } from "@/components/Scrubber";
import { PlayerBar } from "@/components/PlayerBar";
import { StatsRail } from "@/components/StatsRail";
import { buildFrames } from "@/lib/animation";
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

  const { turns, ended, notices } = useMemo(
    () => foldEvents(eventsThroughPly(events, ply), []),
    [events, ply],
  );

  /* Every position, derived once. Scrubbing used to rebuild the board from the starting position
     on every step — a `new Chess()` and the whole game replayed through it, which is ~3.4ms for a
     63-ply game and grows with its length. The moves still come from the event log rather than
     from `game.moves`, so replay is reading the same rows a spectator saw (ADR-0008); only the
     *number of times* it reads them has changed.

     `buildFrames` is the same function the landing page's self-playing thumbnails use, so the two
     cannot disagree about what a position is. */
  const frames = useMemo(
    () => buildFrames(game.start_fen, foldEvents(events, []).moves),
    [game.start_fen, events],
  );

  const frame = frames[Math.min(ply, frames.length - 1)] ?? frames[0];
  const fen = frame.fen;
  const lastMove = frame.lastMove;
  /* Field two of a FEN is the side to move. Cheaper than a `Chess` instance, and the position
     mid-scrub is never terminal — the final ply is handled by `atEnd` below. */
  const toMove = fen.split(" ")[1] === "w" ? "white" : "black";

  /* Captures at the ply being shown, not at the end — scrubbing back should show the material as
     it stood then, which is half of what makes a replay worth scrubbing. */
  const { taken, advantage } = useMemo(() => {
    const { white, black, advantage: lead } = captures(fen);
    return { taken: { white, black }, advantage: lead };
  }, [fen]);

  const turnIds = useMemo(() => turnIdsByPly(turnRows), [turnRows]);

  /* Stable, so `EventStream`'s memoised rows can skip a render they do not need. */
  const inspect = useCallback(
    (turn: TurnView) => setInspecting((current) => (turnIds.has(turn.ply) ? turn : current)),
    [turnIds],
  );
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
    <>
      <GameLayout
        header={<Header game={game} ended={ended} ply={ply} total={total} actions={actions} />}
        streamLabel="Moves"
        board={
          <>
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
          </>
        }
        stream={
          <EventStream
            turns={turns}
            notices={notices}
            players={game.players}
            emptyMessage="The starting position — step forward to begin."
            focusKey={focus?.key ?? null}
            onInspect={inspect}
            header={
              /* The transport sits with the conversation rather than under the board: it is what
                 scrubs both, and taking it out of the centre column gives the board back the
                 height that is the only thing limiting how large it can be.
                 It rides the panel on a phone too, so it stays reachable under the board rather
                 than behind the Info tab. */
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
        }
        stats={<StatsRail game={game} toMove={sideToMove} activePly={ply} />}
      />

      {inspecting && turnIds.has(inspecting.ply) && (
        <RawTranscript
          apiUrl={apiUrl}
          gameId={game.id}
          turnId={turnIds.get(inspecting.ply)!}
          label={`ply ${inspecting.ply} · ${inspecting.model || inspecting.colour}`}
          onClose={() => setInspecting(null)}
        />
      )}
    </>
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

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

import { useCallback, useMemo, useState } from "react";
import { Chess } from "chess.js";

import { Board } from "@/components/Board";
import { EventStream } from "@/components/EventStream";
import { PlayerBar } from "@/components/PlayerBar";
import { StatsRail } from "@/components/StatsRail";
import { legalTargets } from "@/lib/board";
import { captures } from "@/lib/captures";
import { useGameDetail } from "@/hooks/useGameDetail";
import { useGameStream } from "@/hooks/useGameStream";
import { foldEvents } from "@/lib/turns";
import type { Colour, GameDetail, GameEvent } from "@/lib/types";

const TERMINAL = new Set(["finished", "aborted"]);

export function LiveGame({
  game: initial,
  apiUrl,
  initialEvents,
  actions,
  seat,
  onMove,
  controls,
}: {
  game: GameDetail;
  apiUrl: string;
  initialEvents: GameEvent[];
  /** Copy-link and PGN. They ride the status row rather than a bar of their own (UI feedback). */
  actions?: React.ReactNode;
  /**
   * The colour the viewer is playing, when they hold a seat (HUMAN-01). Absent for spectators,
   * which is what keeps this component usable for the model-vs-model case it was built for.
   */
  seat?: "white" | "black";
  /**
   * Called when the viewer drops a piece on a legal square. Fired after the move has been
   * validated locally, but the server is still the authority — a move it refuses is undone by the
   * next position the stream delivers (invariant 1).
   */
  onMove?: (san: string, expectedPly: number) => void;
  /** Resign, draw, and chat controls. Rendered under the board so they sit near the action. */
  controls?: React.ReactNode;
}) {
  /* Subscribe from the last event we actually hold, not from the cursor on the game record.
     The page fetches the record and the event list as two requests, so an event appended between
     them leaves `event_seq` behind what `initialEvents` already contains — and the stream then
     replays events the panel is holding. `foldEvents` dedupes regardless, but starting from the
     right cursor means those events are never sent twice in the first place. */
  const afterSeq = Math.max(initial.event_seq, initialEvents.at(-1)?.seq ?? 0);

  const { events, status } = useGameStream({
    gameId: initial.id,
    apiUrl,
    afterSeq,
    enabled: !TERMINAL.has(initial.status),
  });

  // History first, then whatever has streamed in since. The move list is rebuilt from
  // `move_made` events rather than from `game.moves`, so there is exactly one source of truth and
  // no chance of counting a move twice.
  const { turns, moves, ended, notices, paused } = useMemo(
    () => foldEvents([...initialEvents, ...events], []),
    [initialEvents, events],
  );

  // Stats are not in the event stream; refetch the record as plies land, or the rail shows the
  // numbers as they were when the page loaded and never moves again.
  const game = useGameDetail({
    initial,
    apiUrl,
    plyCount: moves.length,
    ended: ended !== null,
    /* A pause is the other moment the record changes without a ply. Without it the header kept
       pulsing "live" over a board the harness had stopped — which is the whole thing the `paused`
       branch below was written to prevent, arriving through a status the page never refetched. */
    statusSeq: paused?.seq ?? 0,
  });

  const { fen, lastMove, toMove } = useMemo(() => {
    const board = new Chess(initial.start_fen);
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
  }, [moves, initial.start_fen]);

  const outcome = ended ?? terminalFrom(game);
  const yourMove = Boolean(seat && onMove && !outcome && toMove === seat);

  /* Validated locally before it leaves the browser, so a wrong drag is refused without a round
     trip — but this is a courtesy, not the rule. The same move is checked again by the referee
     server-side, which is what a crafted request meets (invariant 1). */
  /**
   * A drag that lands a pawn on the last rank, held until the player says what it becomes.
   *
   * It used to promote to a queen silently. A queen is right almost every time, which is exactly
   * why the exception matters: the one position in a game where a rook or a knight wins is a
   * position the player cannot reach, and they find that out by watching the wrong piece appear.
   */
  const [promoting, setPromoting] = useState<{ from: string; to: string } | null>(null);

  function play(from: string, to: string, promotion?: "q" | "r" | "b" | "n"): boolean {
    if (!onMove) return false;
    const board = new Chess(fen);
    try {
      /* Validated locally before it leaves the browser, so a wrong drag is refused without a round
         trip — a courtesy, not the rule. The referee checks it again (invariant 1). */
      const move = board.move({ from, to, promotion });
      onMove(move.san, moves.length);
      return true;
    } catch {
      return false;
    }
  }

  function handleDrop(from: string, to: string): boolean {
    if (!onMove) return false;

    const board = new Chess(fen);
    const promotes = board
      .moves({ square: from as Parameters<typeof board.moves>[0]["square"], verbose: true })
      .some((move) => move.to === to && move.promotion);

    if (promotes) {
      // Held, not played. `true` so the board accepts the drag and shows the pawn on its new
      // square while the choice is made — reverting it first and moving it again reads as a
      // rejected move.
      setPromoting({ from, to });
      return true;
    }
    return play(from, to);
  }

  /* Near is the seat at the bottom of the board, which is the viewer's own when they hold one. */
  const nearColour = seat ?? "white";
  const farColour = nearColour === "white" ? "black" : "white";
  const near = game.players.find((p) => p.colour === nearColour);
  const far = game.players.find((p) => p.colour === farColour);

  const { taken, advantage } = useMemo(() => {
    const { white, black, advantage: lead } = captures(fen);
    return {
      taken: { white, black },
      // Only the side that is ahead shows a number; +0 beside both names says nothing twice.
      advantage: { white: Math.max(0, lead), black: Math.max(0, -lead) },
    };
  }, [fen]);

  /* Where the piece on a square may go, for the dots and for click-to-move. Handed to the board
     rather than derived inside it: the position lives here, and the board knows no chess. */
  const targetsFor = useCallback(
    (square: string) => (yourMove ? legalTargets(fen, square) : []),
    [yourMove, fen],
  );

  return (
    <div className="flex flex-col gap-4">
      <Header game={game} status={status} outcome={outcome} actions={actions} />

      {/* One expression does the whole layout.
          The board is square, so its size is bounded by whichever runs out first — the height left
          under the page chrome, or a reasonable share of the width. `min()` of those two is the
          board column; the two rails are `1fr` each and split everything else, so there is never a
          dead gutter and never a horizontal scrollbar.
          Deriving the board's width from its own height instead is the obvious idea and does not
          work: in a grid an `auto` column must resolve its width before the row height is known,
          and in flex the same knot ties itself the other way. Both were tried. */}
      <div className="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-9.5rem)] lg:grid-cols-[minmax(0,1fr)_min(calc(100dvh-12rem),52vw)_minmax(0,1fr)]">
        {/* Stacked on a phone the order is board, conversation, stats: the conversation is the
            reason someone opened the page, and burying it under the full stats rail means
            scrolling past telemetry to reach the fight (ADR-0013). */}
        <div className="order-3 min-w-0 overflow-y-auto lg:order-none">
          <StatsRail game={game} toMove={toMove} />
        </div>

        <div className="order-1 flex min-h-0 min-w-0 flex-col gap-2 lg:order-none">
          {/* Each player sits on the side of the board their pieces are on: the near seat below,
              the far seat above. A viewer playing Black has the board turned round, so the two
              swap with it. */}
          <PlayerBar
            player={far}
            taken={taken[farColour]}
            advantage={advantage[farColour]}
            active={toMove === farColour}
            toMoveLabel={outcome ? outcome.result : toMove === farColour ? "to move" : null}
          />
          <Board
            fen={fen}
            lastMove={lastMove}
            /* The board turns round for a person playing Black. Reading a mirrored board is
               possible and unpleasant, and nobody plays well doing it. */
            orientation={seat ?? "white"}
            onDrop={yourMove ? handleDrop : undefined}
            targetsFor={yourMove ? targetsFor : undefined}
          />
          <PlayerBar
            player={near}
            taken={taken[nearColour]}
            advantage={advantage[nearColour]}
            active={toMove === nearColour}
            toMoveLabel={toMove === nearColour && !outcome ? "to move" : null}
          />
        </div>

        {promoting && (
          <PromotionPicker
            colour={seat ?? "white"}
            onPick={(piece) => {
              play(promoting.from, promoting.to, piece);
              setPromoting(null);
            }}
            onCancel={() => setPromoting(null)}
          />
        )}

        <div className="order-2 flex min-h-[24rem] min-w-0 flex-col lg:order-none lg:min-h-0">
          {/* Resign, draw and chat live here rather than under the board. They are things said to
              an opponent, and this is the column where everything said to an opponent already is —
              under the board they crowded the one element that wants the room. */}
          <EventStream
            turns={turns}
            notices={notices}
            players={game.players}
            footer={controls}
            emptyMessage={
              paused ? `Paused — ${paused.text}` : undefined
            }
          />
        </div>
      </div>
    </div>
  );
}

/**
 * Which piece the pawn becomes.
 *
 * Queen first and focused, because it is the answer almost every time; the other three are there
 * for the position where they are not. Modal rather than inline: a move is not made until this is
 * answered, and a picker that could be ignored would leave the board in a state the server has
 * never heard of.
 */
function PromotionPicker({
  colour,
  onPick,
  onCancel,
}: {
  colour: Colour;
  onPick: (piece: "q" | "r" | "b" | "n") => void;
  onCancel: () => void;
}) {
  const pieces: { key: "q" | "r" | "b" | "n"; label: string; glyph: string }[] = [
    { key: "q", label: "Queen", glyph: colour === "white" ? "♕" : "♛" },
    { key: "r", label: "Rook", glyph: colour === "white" ? "♖" : "♜" },
    { key: "b", label: "Bishop", glyph: colour === "white" ? "♗" : "♝" },
    { key: "n", label: "Knight", glyph: colour === "white" ? "♘" : "♞" },
  ];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose a promotion"
      className="fixed inset-0 z-50 flex items-center justify-center bg-ground/80 p-5 backdrop-blur-sm"
      onKeyDown={(event) => event.key === "Escape" && onCancel()}
    >
      <div className="border border-line bg-surface-2 p-5">
        <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Promote to
        </p>
        <div className="flex gap-2">
          {pieces.map((piece, index) => (
            <button
              key={piece.key}
              type="button"
              autoFocus={index === 0}
              onClick={() => onPick(piece.key)}
              className="flex h-16 w-16 flex-col items-center justify-center gap-0.5 border border-line bg-surface transition-colors hover:border-accent focus-visible:border-accent"
            >
              <span aria-hidden className="text-3xl leading-none text-ink">
                {piece.glyph}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
                {piece.label}
              </span>
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="mt-3 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint hover:text-ink-dim"
        >
          cancel
        </button>
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
  actions,
}: {
  game: GameDetail;
  status: string;
  outcome: { result: string; termination: string; detail: string } | null;
  actions?: React.ReactNode;
}) {
  /* `status` is the *connection*, not the game. Showing it as the game's state is how a finished
     game kept claiming to be live: the SSE stream sits open, so "live" stayed true long after the
     last move. The game's own record decides, and the connection is a smaller note beside it. */
  const finished = outcome !== null || TERMINAL.has(game.status);

  return (
    <div className="flex flex-wrap items-center gap-3">
      {finished ? (
        <span className="border border-good px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-good">
          {outcome?.result || game.result}
          {(outcome?.termination || game.termination) &&
            ` · ${outcome?.termination || game.termination}`}
        </span>
      ) : game.status === "paused" ? (
        /* Not live and not over. The dot does not pulse, because nothing is happening — a pulsing
           "live" over a board that has stopped moving is the thing this whole change exists to
           stop. The reason travels with it: a stopped game with no explanation reads as broken. */
        <span
          className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-bad"
          title={game.pause_reason ?? undefined}
        >
          <i aria-hidden className="block h-1.5 w-1.5 rounded-full bg-bad" />
          paused
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

      {/* Plies move as the game does, so this is the readout that shows it is still going. */}
      <span className="tabular font-mono text-[10px] text-ink-faint">
        {game.ply_count} plies
      </span>
      <span className="font-mono text-[10px] text-ink-faint">game {game.id.slice(0, 8)}</span>
      {(outcome?.detail || game.termination_detail) && (
        <span className="text-xs text-ink-dim">
          {outcome?.detail || game.termination_detail}
        </span>
      )}
      {/* Wrapped rather than rendered bare. `actions` is built in a Server Component and crosses
          the RSC boundary into this client one, which lands it in the children array without a
          key — React warns. A wrapper gives it a single-child slot instead of a list position. */}
      {actions && <span className="ml-auto flex items-center">{actions}</span>}
    </div>
  );
}

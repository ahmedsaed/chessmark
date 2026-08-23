"use client";

/**
 * A game you are playing (Phase 10).
 *
 * Wraps `LiveGame` rather than reimplementing it. The layout, the event fold, the stats rail and
 * the conversation are all identical to spectating, because they should be — the only difference
 * is that the board accepts drops and there are controls under it. Building a second game view
 * would guarantee the two drift.
 *
 * Clerk lives here rather than in `LiveGame` so that component stays usable on a page with no
 * account and no provider, which is how spectating works (AUTH-02).
 */

import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { LiveGame } from "@/components/LiveGame";
import {
  ApiError,
  offerDraw,
  resignGame,
  respondToDraw,
  sayToModel,
  sendMove,
} from "@/lib/api";
import type { GameDetail, GameEvent } from "@/lib/types";

export function PlayableGame({
  game,
  apiUrl,
  initialEvents,
  actions,
  seat,
  drawOffered,
}: {
  game: GameDetail;
  apiUrl: string;
  initialEvents: GameEvent[];
  actions?: React.ReactNode;
  seat: "white" | "black";
  /** True when the model has an open draw offer for this position. */
  drawOffered: boolean;
}) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const act = useCallback(
    async (run: (token: string | null) => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await run(await getToken());
        // The stream carries the model's reply, but the *record* — status, cost, the seat's own
        // move — comes from the server. Refreshing keeps the rails honest rather than guessing.
        router.refresh();
      } catch (failure) {
        setError(
          failure instanceof ApiError
            ? failure.message
            : "That did not reach the server. Check your connection and try again.",
        );
      } finally {
        setBusy(false);
      }
    },
    [getToken, router],
  );

  const handleMove = useCallback(
    (san: string, expectedPly: number) => {
      void act((token) => sendMove(game.id, token, san, expectedPly));
    },
    [act, game.id],
  );

  return (
    <LiveGame
      game={game}
      apiUrl={apiUrl}
      initialEvents={initialEvents}
      actions={actions}
      seat={seat}
      onMove={handleMove}
      controls={
        <Controls
          busy={busy}
          error={error}
          drawOffered={drawOffered}
          onResign={() => act((token) => resignGame(game.id, token))}
          onOfferDraw={() => act((token) => offerDraw(game.id, token))}
          onAnswerDraw={(accept) => act((token) => respondToDraw(game.id, token, accept))}
          onSay={(message) => act((token) => sayToModel(game.id, token, message))}
          talkEnabled={game.trash_talk_enabled}
        />
      }
    />
  );
}

function Controls({
  busy,
  error,
  drawOffered,
  onResign,
  onOfferDraw,
  onAnswerDraw,
  onSay,
  talkEnabled,
}: {
  busy: boolean;
  error: string | null;
  drawOffered: boolean;
  onResign: () => void;
  onOfferDraw: () => void;
  onAnswerDraw: (accept: boolean) => void;
  onSay: (message: string) => void;
  talkEnabled: boolean;
}) {
  const [message, setMessage] = useState("");
  const [confirmResign, setConfirmResign] = useState(false);

  return (
    <div className="flex flex-none flex-col gap-2">
      {drawOffered && (
        <div className="flex flex-wrap items-center gap-2 border border-machine-dim bg-surface-2 px-3 py-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-machine">
            Draw offered
          </span>
          <button type="button" disabled={busy} onClick={() => onAnswerDraw(true)} className={ACCEPT}>
            accept
          </button>
          <button type="button" disabled={busy} onClick={() => onAnswerDraw(false)} className={PLAIN}>
            decline
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy} onClick={onOfferDraw} className={PLAIN}>
          offer draw
        </button>

        {/* Two steps, because resigning is irreversible and a stray click should not end a game. */}
        {confirmResign ? (
          <>
            <button type="button" disabled={busy} onClick={onResign} className={DANGER}>
              confirm resign
            </button>
            <button type="button" onClick={() => setConfirmResign(false)} className={PLAIN}>
              cancel
            </button>
          </>
        ) : (
          <button type="button" disabled={busy} onClick={() => setConfirmResign(true)} className={PLAIN}>
            resign
          </button>
        )}
      </div>

      {talkEnabled && (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const text = message.trim();
            if (!text) return;
            onSay(text);
            setMessage("");
          }}
        >
          <input
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            maxLength={500}
            placeholder="Say something to your opponent…"
            className="min-w-0 flex-1 border border-line bg-surface px-2 py-1 font-mono text-[11px] text-ink placeholder:text-ink-faint focus:border-accent-dim focus:outline-none"
          />
          <button type="submit" disabled={busy || !message.trim()} className={PLAIN}>
            send
          </button>
        </form>
      )}

      {error && <p className="font-mono text-[10px] text-bad">{error}</p>}
    </div>
  );
}

const PLAIN =
  "border border-line bg-surface px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink disabled:opacity-40";
const ACCEPT =
  "border border-accent-deep bg-accent px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40";
const DANGER =
  "border border-bad bg-bad-deep px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink transition-colors hover:bg-bad disabled:opacity-40";

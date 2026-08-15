"use client";

/**
 * Subscribe to a game's event stream.
 *
 * `EventSource` handles reconnection itself and replays `Last-Event-ID` automatically, which is
 * most of why ADR-0004 chose SSE. The server answers that header by replaying exactly the missed
 * events from `game_events`, so a dropped connection costs nothing (UI-10).
 *
 * The one thing the browser does *not* do is give us a starting cursor. A page rendered on the
 * server already knows the game up to `event_seq`, so the first connection passes it explicitly —
 * without it the client would replay the whole game and re-animate every move on load.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { GameEvent } from "@/lib/types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

interface Options {
  gameId: string;
  apiUrl: string;
  /** Events at or below this are already reflected in the server-rendered page. */
  afterSeq: number;
  /** Skip connecting entirely for a game that already finished. */
  enabled?: boolean;
}

const EVENT_TYPES = [
  "game_started",
  "turn_started",
  "thinking",
  "tool_called",
  "illegal_attempt",
  "move_made",
  "message_sent",
  "draw_offered",
  "game_ended",
] as const;

export function useGameStream({ gameId, apiUrl, afterSeq, enabled = true }: Options) {
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>(enabled ? "connecting" : "closed");

  // The cursor lives in a ref so reconnecting never re-runs the effect: putting it in state
  // would tear down and rebuild the EventSource on every single event.
  const cursor = useRef(afterSeq);
  const seen = useRef<Set<number>>(new Set());

  const push = useCallback((event: GameEvent) => {
    if (seen.current.has(event.seq)) return;
    seen.current.add(event.seq);
    cursor.current = Math.max(cursor.current, event.seq);
    setEvents((previous) => [...previous, event]);
  }, []);

  useEffect(() => {
    // No setState here: `enabled` is fixed for the component's life (it comes from the
    // server-rendered game status), so the initial state above already says "closed". Setting it
    // again inside the effect would only trigger a cascading render.
    if (!enabled) return;

    const source = new EventSource(
      `${apiUrl}/games/${gameId}/stream?after_seq=${cursor.current}`,
    );
    let torndown = false;

    const handle = (raw: Event) => {
      const message = raw as MessageEvent<string>;
      setStatus("live");
      try {
        const parsed = JSON.parse(message.data) as GameEvent;
        if (typeof parsed?.seq === "number") push(parsed);
      } catch {
        // A frame we cannot parse is not worth tearing the stream down for.
      }
    };

    source.onopen = () => setStatus("live");
    source.onmessage = handle;

    // The server names each frame after its event type, so a bare `onmessage` never fires for
    // them. Every type has to be registered explicitly.
    for (const type of EVENT_TYPES) source.addEventListener(type, handle);

    source.onerror = () => {
      if (torndown) return;
      // EventSource reconnects on its own; CLOSED means it has given up for good.
      setStatus(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    };

    return () => {
      torndown = true;
      source.close();
    };
  }, [gameId, apiUrl, enabled, push]);

  return { events, status };
}

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
 *
 * **Two kinds of frame arrive here** (ADR-0035). Numbered ones are committed events and are the
 * record. `delta` frames are what the turn is *doing*: a turn is one transaction, so a
 * ten-minute one used to deliver every event at once at the end, and these arrive as each round
 * finishes. They carry no `seq`, are never stored, and are replaced by the committed events
 * moments later — so they are kept apart from `events` entirely, and a component that ignores
 * them is correct.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { GameEvent, LiveFrame } from "@/lib/types";

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
  const [live, setLive] = useState<LiveFrame[]>([]);
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
    /* **A committed event supersedes every frame that predicted it.** They describe the same
       turn, so keeping both would draw each step twice — once provisionally and once for real.
       `turn_started` is the boundary: it is the first thing a turn appends, so a turn's own
       events never clear its own frames, and the next turn's arrival clears the last one's. */
    if (event.type === "turn_started" || event.type === "move_made") setLive([]);
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

    /* Unnumbered and unrecorded, so it never touches the cursor: `Last-Event-ID` must go on
       naming a committed event or a reconnect would resume from something never written down. */
    const handleDelta = (raw: Event) => {
      setStatus("live");
      try {
        const frame = JSON.parse((raw as MessageEvent<string>).data) as LiveFrame;
        /* **Every kind, `turn` included.** This listed `block` and `token` and dropped `turn`,
           which was added afterwards so a spectator could be shown a turn whose `turn_started` is
           still inside an uncommitted transaction. `liveTurn` hangs the blocks off that frame and
           returns null without it — so every frame arrived, was thrown away, and the panel went on
           showing the turn only once it committed. The whole feature was invisible and nothing
           errored. */
        if (frame?.frame === "turn" || frame?.frame === "block" || frame?.frame === "token") {
          setLive((previous) => [...previous, frame]);
        }
      } catch {
        // A frame we cannot parse costs a flicker, never a wrong transcript.
      }
    };

    source.onopen = () => setStatus("live");
    source.onmessage = handle;
    source.addEventListener("delta", handleDelta);

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

  return { events, live, status };
}

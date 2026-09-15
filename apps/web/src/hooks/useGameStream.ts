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
import { supersedesFrames } from "@/lib/turns";
import type { EventType, GameEvent, LiveFrame } from "@/lib/types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

interface Options {
  gameId: string;
  apiUrl: string;
  /** Events at or below this are already reflected in the server-rendered page. */
  afterSeq: number;
  /** Skip connecting entirely for a game that already finished. */
  enabled?: boolean;
}

/**
 * Every event type, because the server names each SSE frame after its type and a bare `onmessage`
 * never fires for a named one: a type missing here is an event the browser receives and discards.
 *
 * **Keyed by the union, so leaving one out does not compile.** This was a hand-written list, and
 * it was missing `output`, `compacted`, `game_paused` and `game_resumed` — four types added after
 * it. A pause reached the page only on a refresh, which reads the log over HTTP, and the symptom
 * ("the pause is not in the events until I reload") was investigated twice as a publishing
 * problem: the events were being published, delivered, and thrown away three lines from here.
 */
const EVENT_TYPES = Object.keys({
  game_started: true,
  turn_started: true,
  thinking: true,
  output: true,
  tool_called: true,
  illegal_attempt: true,
  move_made: true,
  message_sent: true,
  draw_offered: true,
  compacted: true,
  game_paused: true,
  game_resumed: true,
  game_ended: true,
} satisfies Record<EventType, true>) as EventType[];

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
    // A committed event supersedes the frames that predicted it — `supersedesFrames` is the rule,
    // and it lives beside `withLiveTurn` because the two together decide what the panel draws.
    if (supersedesFrames(event.type)) setLive([]);
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

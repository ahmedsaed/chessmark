/**
 * Assemble the event stream into turns.
 *
 * The conversation panel is a messaging-app timeline where the **move is the date separator**
 * (ADR-0013): everything a model did between two dividers is one coherent thought. That grouping
 * has to be derived here, because the event log is flat.
 */

import type { Colour, GameEvent, TurnView } from "@/lib/types";

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * One event per `seq`, in ascending order.
 *
 * The page seeds the panel with a history fetch and then subscribes to the stream from a cursor,
 * and **the two can overlap**: the game record and the event list are two separate requests, so an
 * event appended between them is both in the history and replayed by the stream. That really
 * happens — a live game with a worker appending turn events produced two `turn_started` rows at
 * the same `seq`, which React reported as a duplicate key and which would have counted the same
 * `move_made` twice in the move list.
 *
 * `seq` is gap-free and unique per game (ADR-0008), so it is the identity. Deduping here rather
 * than at the seam makes the guarantee `LiveGame` already claims — that a duplicated or
 * out-of-order event cannot desync the view — true of the conversation and the move list, not just
 * of the board.
 */
function dedupe(events: GameEvent[]): GameEvent[] {
  const bySeq = new Map<number, GameEvent>();
  for (const event of events) {
    if (!bySeq.has(event.seq)) bySeq.set(event.seq, event);
  }
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

export interface StreamState {
  turns: TurnView[];
  moves: string[];
  ended: { result: string; termination: string; detail: string } | null;
}

/**
 * Fold events into turns and the move list.
 *
 * Pure and total: it takes every event seen so far and rebuilds the view from scratch. That costs
 * a little work per render and buys a guarantee worth far more — a reconnect that replays events
 * cannot leave the panel in a state that incremental patching would have produced.
 */
export function foldEvents(events: GameEvent[], initialMoves: string[]): StreamState {
  const turns: TurnView[] = [];
  const moves = [...initialMoves];
  let ended: StreamState["ended"] = null;
  let current: TurnView | null = null;

  /**
   * Open a turn for an event that arrives without one.
   *
   * **A person's actions have no `turn_started`.** The worker emits one before a model thinks;
   * `orchestration/human.py` emits only the action itself, because a human turn has no provider
   * call to bracket. So a human's move landed in the move list with no turn to belong to, and the
   * conversation showed the model talking to nobody — every human ply simply missing from the
   * timeline it was half of.
   *
   * Synthesising it here rather than appending a `turn_started` server-side keeps the event log
   * describing what actually happened: nothing started a turn, a person just moved.
   */
  const openTurn = (event: GameEvent, payload: Record<string, unknown>): TurnView => {
    const turn: TurnView = {
      key: `turn-${event.seq}`,
      ply: asNumber(payload.ply),
      colour: (asString(payload.colour) || "white") as Colour,
      playerId: asString(payload.player_id),
      model: asString(payload.model),
      human: payload.human === true,
      reasoning: [],
      output: [],
      tools: [],
      illegal: [],
      said: [],
      san: null,
      live: false,
    };
    turns.push(turn);
    return turn;
  };

  /** The turn an event belongs to, opening one if the last is closed or absent. */
  const turnFor = (event: GameEvent, payload: Record<string, unknown>): TurnView => {
    if (current && current.san === null) return current;
    current = openTurn(event, payload);
    return current;
  };

  for (const event of dedupe(events)) {
    const payload = event.payload ?? {};

    switch (event.type) {
      case "turn_started": {
        current = {
          key: `turn-${event.seq}`,
          ply: asNumber(payload.ply),
          colour: (asString(payload.colour) || "white") as Colour,
          playerId: asString(payload.player_id),
          model: asString(payload.model),
          human: payload.human === true,
          reasoning: [],
          output: [],
          tools: [],
          illegal: [],
          said: [],
          san: null,
          live: true,
        };
        turns.push(current);
        break;
      }

      case "thinking": {
        // Absent in human games — the text is withheld from the stream so the human at the table
        // cannot read their opponent's plan (invariant 8).
        const text = asString(payload.reasoning);
        if (current && text) current.reasoning.push(text);
        break;
      }

      case "output": {
        // Prose the model wrote outside a tool call. Kept apart from `reasoning` because
        // providers split the two differently and a reader wants to know which they are seeing.
        const text = asString(payload.content);
        if (current && text.trim()) current.output.push(text);
        break;
      }

      case "tool_called": {
        if (current) {
          current.tools.push({
            name: asString(payload.tool),
            ok: payload.ok !== false,
            args: asRecord(payload.args),
            result: payload.result === undefined ? null : asRecord(payload.result),
          });
        }
        break;
      }

      case "illegal_attempt": {
        if (current) {
          current.illegal.push({
            move: asString(payload.move),
            detail: asString(payload.detail),
            attempt: asNumber(payload.attempt),
          });
        }
        break;
      }

      case "message_sent": {
        /* `content` is what a model's `say` writes and what this always read. A person's `say`
           wrote `message` instead, so nothing a human typed ever reached the panel — stored and
           delivered to the model, invisible on the page. The backend writes `content` now; this
           reads both, because the event log is append-only and the old rows are still there. */
        const text = asString(payload.content) || asString(payload.message);
        if (text) turnFor(event, payload).said.push(text);
        break;
      }

      case "move_made": {
        const san = asString(payload.san);
        if (san) moves.push(san);
        const turn = turnFor(event, payload);
        turn.san = san;
        turn.live = false; // the move closes the turn, so it folds
        break;
      }

      case "game_ended": {
        if (current) current.live = false;
        ended = {
          result: asString(payload.result),
          termination: asString(payload.termination),
          detail: asString(payload.detail),
        };
        break;
      }

      default:
        break;
    }
  }

  return { turns, moves, ended };
}

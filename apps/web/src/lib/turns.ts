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

  for (const event of events) {
    const payload = event.payload ?? {};

    switch (event.type) {
      case "turn_started": {
        current = {
          key: `turn-${event.seq}`,
          ply: asNumber(payload.ply),
          colour: (asString(payload.colour) || "white") as Colour,
          playerId: asString(payload.player_id),
          model: asString(payload.model),
          reasoning: [],
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

      case "tool_called": {
        if (current) {
          current.tools.push({
            name: asString(payload.tool),
            ok: payload.ok !== false,
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
        if (current) current.said.push(asString(payload.content));
        break;
      }

      case "move_made": {
        const san = asString(payload.san);
        if (san) moves.push(san);
        if (current) {
          current.san = san;
          current.live = false; // the move closes the turn, so it folds
        }
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

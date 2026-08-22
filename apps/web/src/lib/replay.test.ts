/**
 * Replay slicing.
 *
 * Phase 8's headline criterion — "scrubbing to ply N shows exactly the board, reasoning, tool
 * calls, and messages as of ply N" — is a property of these two functions, so it is asserted here
 * rather than clicked through. The strongest test in the file is the last one: it replays every
 * ply and checks each against what the live view produced at that moment.
 */

import { describe, expect, it } from "vitest";

import { eventsThroughPly, plyCount, turnIdsByPly } from "@/lib/replay";
import { foldEvents } from "@/lib/turns";
import type { GameEvent, EventType } from "@/lib/types";

let seq = 0;
function event(type: EventType, payload: Record<string, unknown> = {}): GameEvent {
  seq += 1;
  return { seq, type, payload };
}

/** One turn's worth of events: think, call a tool, maybe taunt, then move. */
function turn(ply: number, colour: "white" | "black", san: string, said?: string): GameEvent[] {
  const events = [
    event("turn_started", { ply, colour, player_id: colour, model: `${colour}-model` }),
    event("thinking", { reasoning: `plan for ${san}` }),
    event("tool_called", { tool: "get_legal_moves", ok: true }),
  ];
  if (said) events.push(event("message_sent", { content: said }));
  events.push(event("move_made", { ply, colour, san }));
  return events;
}

const GAME: GameEvent[] = [
  event("game_started", {}),
  ...turn(1, "white", "e4", "watch this"),
  ...turn(2, "black", "e5"),
  ...turn(3, "white", "Nf3"),
  ...turn(4, "black", "Nc6", "not today"),
  event("game_ended", { result: "1/2-1/2", termination: "ply_cap", detail: "capped" }),
];

// ====================================================================== counting

describe("plyCount", () => {
  it("counts moves, not events", () => {
    expect(plyCount(GAME)).toBe(4);
  });

  it("is zero for a game that never moved", () => {
    expect(plyCount([event("game_started", {}), event("turn_started", { ply: 1 })])).toBe(0);
  });

  it("is zero for an empty log", () => {
    expect(plyCount([])).toBe(0);
  });
});

// ====================================================================== slicing

describe("eventsThroughPly", () => {
  it("ply 0 is the starting position with nothing said or thought", () => {
    const { turns, moves, ended } = foldEvents(eventsThroughPly(GAME, 0), []);

    expect(moves).toEqual([]);
    expect(turns).toEqual([]);
    expect(ended).toBeNull();
  });

  it("ply N shows exactly N moves", () => {
    for (let ply = 0; ply <= 4; ply += 1) {
      const { moves } = foldEvents(eventsThroughPly(GAME, ply), []);
      expect(moves).toHaveLength(ply);
    }
  });

  it("stops at the move, so the next turn's reasoning is not yet visible", () => {
    const { turns } = foldEvents(eventsThroughPly(GAME, 1), []);

    expect(turns).toHaveLength(1);
    expect(turns[0].san).toBe("e4");
    expect(turns[0].reasoning).toEqual(["plan for e4"]);
  });

  it("does not leak a later turn's taunt", () => {
    const { turns } = foldEvents(eventsThroughPly(GAME, 2), []);
    const said = turns.flatMap((t) => t.said);

    expect(said).toEqual(["watch this"]);
    expect(said).not.toContain("not today");
  });

  it("the final ply carries the ending", () => {
    const { ended } = foldEvents(eventsThroughPly(GAME, 4), []);

    expect(ended).not.toBeNull();
    expect(ended?.termination).toBe("ply_cap");
  });

  it("an earlier ply does not show the ending", () => {
    expect(foldEvents(eventsThroughPly(GAME, 3), []).ended).toBeNull();
  });

  it("a ply past the end is clamped to the whole log", () => {
    expect(eventsThroughPly(GAME, 99)).toEqual(GAME);
    expect(eventsThroughPly(GAME, -1)).toEqual([]);
  });

  it("keeps a trailing forfeit turn that never produced a move", () => {
    /* A game can end without a final move — an illegal-move forfeit is a turn with reasoning,
       several illegal attempts, and no `move_made`. Truncating at the last move would erase the
       most interesting turn in the game. */
    const forfeited: GameEvent[] = [
      ...turn(1, "white", "e4"),
      event("turn_started", { ply: 2, colour: "black", model: "black-model" }),
      event("illegal_attempt", { move: "Ke9", detail: "off the board", attempt: 1 }),
      event("game_ended", { result: "1-0", termination: "illegal_move_forfeit", detail: "" }),
    ];

    const { turns, ended } = foldEvents(eventsThroughPly(forfeited, 1), []);

    expect(turns).toHaveLength(2);
    expect(turns[1].illegal).toHaveLength(1);
    expect(ended?.termination).toBe("illegal_move_forfeit");
  });

  it("replaying every ply matches what the live view showed at that moment", () => {
    /* The guarantee the whole module exists for. `live` is excluded from the comparison: the last
       turn is open while a spectator waits for it and folded once the move lands, which is a
       presentational difference, not a difference in what happened. */
    const strip = (events: GameEvent[]) =>
      foldEvents(events, []).turns.map((turn) => ({ ...turn, live: false }));

    for (let ply = 1; ply <= plyCount(GAME); ply += 1) {
      const asLiveSpectatorSaw = strip(GAME.slice(0, GAME.findIndex(
        (e) => e.type === "move_made" && e.payload.ply === ply,
      ) + 1));

      expect(strip(eventsThroughPly(GAME, ply))).toEqual(asLiveSpectatorSaw);
    }
  });
});

// ====================================================================== turn ids

describe("turnIdsByPly", () => {
  it("maps each ply to its turn", () => {
    const map = turnIdsByPly([
      { id: 10, ply_number: 1 },
      { id: 11, ply_number: 2 },
    ]);

    expect(map.get(1)).toBe(10);
    expect(map.get(2)).toBe(11);
  });

  it("ignores turns that produced no move", () => {
    const map = turnIdsByPly([{ id: 10, ply_number: null }]);

    expect(map.size).toBe(0);
  });

  it("prefers the later turn when a ply was retried", () => {
    /* A provider failure rolls its turn back and the ply is replayed, leaving two rows. The
       inspector must open the one that actually produced the move, not the one that died. */
    const map = turnIdsByPly([
      { id: 10, ply_number: 1 },
      { id: 12, ply_number: 1 },
      { id: 11, ply_number: 1 },
    ]);

    expect(map.get(1)).toBe(12);
  });
});

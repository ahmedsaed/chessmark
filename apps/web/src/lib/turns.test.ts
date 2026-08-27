/**
 * Folding the event stream into turns.
 *
 * The overlap cases are the point of this file. The page seeds the panel from a history fetch and
 * then subscribes to the stream from a cursor, and those are two separate requests — so the two
 * sources really do overlap on a live game. It surfaced as a React duplicate-key error
 * (`Encountered two children with the same key, turn-3`) while a person was mid-game against a
 * model, with a worker appending events between the two fetches.
 */

import { describe, expect, it } from "vitest";

import { foldEvents } from "@/lib/turns";
import type { EventType, GameEvent } from "@/lib/types";

let seq = 0;
function event(type: EventType, payload: Record<string, unknown> = {}): GameEvent {
  seq += 1;
  return { seq, type, payload };
}

function turn(ply: number, colour: "white" | "black", san: string): GameEvent[] {
  return [
    event("turn_started", { ply, colour, player_id: colour, model: `${colour}-model` }),
    event("tool_called", { tool: "get_legal_moves", ok: true }),
    event("move_made", { ply, colour, san }),
  ];
}

describe("foldEvents", () => {
  it("builds one turn per turn_started, keyed by its sequence", () => {
    seq = 0;
    const events = [...turn(1, "white", "e4"), ...turn(2, "black", "e5")];

    const { turns, moves } = foldEvents(events, []);

    expect(turns.map((t) => t.san)).toEqual(["e4", "e5"]);
    expect(moves).toEqual(["e4", "e5"]);
    expect(new Set(turns.map((t) => t.key)).size).toBe(2);
  });

  it("keeps turn keys unique when history and stream overlap", () => {
    /* The real failure: the history fetch already held the events the stream then replayed, so
       the same `turn_started` was folded twice and React saw two children keyed `turn-N`. */
    seq = 0;
    const history = [...turn(1, "white", "e4"), ...turn(2, "black", "e5")];
    const replayed = history.slice(3); // the stream re-sends the second turn

    const { turns } = foldEvents([...history, ...replayed], []);

    const keys = turns.map((t) => t.key);
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("does not count a replayed move twice", () => {
    /* The quieter half of the same bug. A duplicated `move_made` would have put the board a ply
       ahead of the server and desynced everything downstream of the move list. */
    seq = 0;
    const history = [...turn(1, "white", "e4")];

    const { moves } = foldEvents([...history, ...history], []);

    expect(moves).toEqual(["e4"]);
  });

  it("orders events by sequence even when the sources are interleaved out of order", () => {
    seq = 0;
    const first = turn(1, "white", "e4");
    const second = turn(2, "black", "e5");

    const { turns, moves } = foldEvents([...second, ...first], []);

    expect(moves).toEqual(["e4", "e5"]);
    expect(turns.map((t) => t.ply)).toEqual([1, 2]);
  });

  it("gives a human move a turn of its own", () => {
    /* A person's move has no `turn_started` — the worker emits one before a model thinks, and
       `human.py` emits only the action. So a human ply landed in the move list with no turn to
       belong to, and every one of them was missing from the timeline it was half of. */
    seq = 0;
    const events = [
      event("move_made", { ply: 1, colour: "white", player_id: "you", san: "e4", human: true }),
      ...turn(2, "black", "e5"),
    ];

    const { turns, moves } = foldEvents(events, []);

    expect(moves).toEqual(["e4", "e5"]);
    expect(turns).toHaveLength(2);
    expect(turns[0]).toMatchObject({ san: "e4", human: true, playerId: "you", live: false });
    expect(turns[1]).toMatchObject({ san: "e5", human: false });
  });

  it("does not fold a human move into the model turn before it", () => {
    /* The model's turn is already closed by its own move, so the human's must open a new one
       rather than overwrite the `san` of the turn above it. */
    seq = 0;
    const events = [
      ...turn(1, "white", "e4"),
      event("move_made", { ply: 2, colour: "black", player_id: "you", san: "e5", human: true }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns.map((t) => t.san)).toEqual(["e4", "e5"]);
  });

  it("shows what a person said", () => {
    /* A human `say` wrote its text under `message` while the panel read `content`, so nothing a
       person typed ever appeared — stored, delivered to the model, invisible on the page. The
       backend writes `content` now; the old rows are still in an append-only log. */
    seq = 0;
    const legacy = foldEvents(
      [event("message_sent", { ply: 1, colour: "white", player_id: "you", message: "hello", human: true })],
      [],
    );
    seq = 0;
    const current = foldEvents(
      [event("message_sent", { ply: 1, colour: "white", player_id: "you", content: "hello", human: true })],
      [],
    );

    expect(legacy.turns[0]?.said).toEqual(["hello"]);
    expect(current.turns[0]?.said).toEqual(["hello"]);
  });

  it("keeps a person's message on the turn their move opened", () => {
    seq = 0;
    const events = [
      event("move_made", { ply: 1, colour: "white", player_id: "you", san: "e4", human: true }),
      event("message_sent", { ply: 1, colour: "white", player_id: "you", content: "good luck", human: true }),
    ];

    const { turns } = foldEvents(events, []);

    /* The move closed its turn, so the message opens a fresh one rather than attaching to a turn
       the reader has already seen fold. */
    expect(turns).toHaveLength(2);
    expect(turns[1].said).toEqual(["good luck"]);
  });

  it("leaves the live turn expanded until its move lands", () => {
    seq = 0;
    const events = [
      ...turn(1, "white", "e4"),
      event("turn_started", { ply: 2, colour: "black", player_id: "black", model: "m" }),
      event("tool_called", { tool: "get_board", ok: true }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].live).toBe(false);
    expect(turns[1].live).toBe(true);
  });

  it("closes the live turn when the game ends without a move", () => {
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "white", model: "m" }),
      event("game_ended", { result: "0-1", termination: "resignation", detail: "White resigned." }),
    ];

    const { turns, ended } = foldEvents(events, []);

    expect(turns[0].live).toBe(false);
    expect(ended?.termination).toBe("resignation");
  });
});

/**
 * Pauses (OPS-12, OPS-14).
 *
 * A pause is turn-less, which is the whole reason it is carried separately: the failed turn is
 * rolled back whole, so its `turn_started` never reaches the log. Nothing in the timeline can hang
 * a notice off a turn that does not exist.
 */
describe("pauses", () => {
  it("surfaces a pause as a notice and as the current state", () => {
    seq = 0;
    const events = [
      ...turn(1, "white", "e4"),
      event("game_paused", {
        reason: "gemma:free rate-limited by Google AI Studio (upstream_provider_shared_pool)",
        resume_after: "2026-08-27T12:00:00Z",
      }),
    ];

    const { notices, paused } = foldEvents(events, []);

    expect(notices).toHaveLength(1);
    expect(notices[0].kind).toBe("paused");
    expect(notices[0].text).toContain("Google AI Studio");
    expect(paused?.resumeAfter).toBe("2026-08-27T12:00:00Z");
  });

  it("closes the live turn, so nothing is left expanded and thinking", () => {
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w" }),
      event("game_paused", { reason: "rate-limited" }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].live).toBe(false);
  });

  it("a resume clears the pause and leaves both notices in order", () => {
    seq = 0;
    const events = [
      ...turn(1, "white", "e4"),
      event("game_paused", { reason: "rate-limited" }),
      event("game_resumed", { detail: "the wait is over" }),
      ...turn(2, "black", "e5"),
    ];

    const { notices, paused, moves } = foldEvents(events, []);

    expect(paused).toBeNull();
    expect(notices.map((n) => n.kind)).toEqual(["paused", "resumed"]);
    expect(moves).toEqual(["e4", "e5"]);
  });

  it("a game that ended is never reported as paused", () => {
    /* The pair can arrive in either order — a game paused past its patience is abandoned, which
       appends `game_ended` after the last `game_paused` still sitting in the log. A page showing
       "paused, retrying shortly" over a finished game would be promising something untrue. */
    seq = 0;
    const events = [
      event("game_paused", { reason: "rate-limited" }),
      event("game_ended", { result: "*", termination: "abandoned", detail: "gave up" }),
    ];

    const { paused, ended } = foldEvents(events, []);

    expect(paused).toBeNull();
    expect(ended?.termination).toBe("abandoned");
  });

  it("carries the seq, so a notice can be placed between the turns it happened between", () => {
    seq = 0;
    const events = [...turn(1, "white", "e4"), event("game_paused", { reason: "x" })];

    const { turns, notices } = foldEvents(events, []);

    expect(turns[0].seq).toBe(1);
    expect(notices[0].seq).toBeGreaterThan(turns[0].seq);
  });
});

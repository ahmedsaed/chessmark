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

import { compactionText, foldEvents, liveTurn, sameTurnContent } from "@/lib/turns";
import type { EventType, GameEvent, LiveFrame } from "@/lib/types";

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

  /* A halt is a pause too (OPS-19, OPS-20). It arrives as the same event with a different reason
     — the free-model allowance, an empty account, an operator — and the panel must not need to
     know which, or a fourth kind of stop would need a fourth branch here. */
  it("shows a harness halt the same way it shows a busy provider", () => {
    seq = 0;
    const events = [
      event("game_paused", {
        reason: "the harness is halted: the free-model allowance for the day is spent (429)",
        halt_source: "free_tier",
        resume_after: "2026-09-04T00:00:00Z",
      }),
    ];

    const { paused } = foldEvents(events, []);

    expect(paused?.text).toContain("free-model allowance");
    expect(paused?.resumeAfter).toBe("2026-09-04T00:00:00Z");
  });

  /* `paused.seq` is what tells the page to refetch the game record — see `useGameDetail`. A halt
     with no known end still has to move it, or a game stopped at ply 0 would go on rendering the
     server's snapshot, which says "running". */
  it("an open-ended halt still moves the seq the page keys its refetch on", () => {
    seq = 0;
    const paused = foldEvents(
      [event("game_paused", { reason: "the harness is halted: stopped by hand" })],
      [],
    ).paused;

    expect(paused?.seq).toBeGreaterThan(0);
    expect(paused?.resumeAfter).toBeNull();
  });
});

/**
 * Compaction (ADR-0018).
 *
 * Shown in the stream because it changes what the model can see from that point on: a reader
 * wondering why it abandoned a plan it announced at move 12 should find the answer in the timeline
 * rather than in the transcript.
 */
describe("compaction", () => {
  it("surfaces a compaction as a notice with what it folded", () => {
    seq = 0;
    const events = [
      ...turn(1, "white", "e4"),
      event("compacted", { folded: 40, kept: 4, context_tokens: 64_000 }),
      ...turn(2, "black", "e5"),
    ];

    const { notices, moves } = foldEvents(events, []);

    expect(notices.map((n) => n.kind)).toEqual(["compacted"]);
    expect(notices[0].text).toContain("40 messages summarised");
    // Play carries on around it: a compaction is not an interruption of the game.
    expect(moves).toEqual(["e4", "e5"]);
  });

  it("is not a pause", () => {
    /* They share the notice channel and mean opposite things: one is the harness stopping, the
       other is the model housekeeping mid-turn while play continues. */
    seq = 0;
    const { paused } = foldEvents([event("compacted", { folded: 10, kept: 4 })], []);

    expect(paused).toBeNull();
  });

  it("does not close the live turn", () => {
    /* Compaction happens *inside* a turn, before the model answers, so the turn is still open —
       unlike a pause, which stops it. Folding the turn here would collapse the panel mid-think. */
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w" }),
      event("compacted", { folded: 10, kept: 4 }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].live).toBe(true);
  });

  it("reads without the counts, since an older payload may not carry them", () => {
    seq = 0;
    const { notices } = foldEvents([event("compacted", {})], []);

    expect(notices[0].text).toBe("history compacted");
  });

  it("says what the pass actually freed", () => {
    /* Every one of these numbers was already in the event and none of it was on screen, which is
       how one game "compacted" five times without ever making room (ADR-0021). */
    seq = 0;
    const { notices } = foldEvents(
      [
        event("compacted", {
          folded: 26,
          trimmed: 4,
          kept: 12,
          characters_before: 400_000,
          characters_after: 40_000,
          occupied_tokens: 261_751,
          context_tokens: 256_000,
        }),
      ],
      [],
    );

    expect(notices[0].text).toContain("26 messages summarised");
    expect(notices[0].text).toContain("4 stale tool results dropped");
    expect(notices[0].text).toContain("90% smaller");
    expect(notices[0].text).toContain("was 262k of 256k tokens");
  });

  it("reports a trim-only pass without claiming anything was summarised", () => {
    /* Rung one needs no provider call at all, and saying "summarised" would be a lie about where
       the tokens went. */
    seq = 0;
    const { notices } = foldEvents(
      [event("compacted", { folded: 0, trimmed: 3, kept: 12 })],
      [],
    );

    expect(notices[0].text).toContain("3 stale tool results dropped");
    expect(notices[0].text).not.toContain("summarised");
  });
});

describe("a resumed game is no longer over", () => {
  it("clears the ending, because the log keeps it", () => {
    /* `game_events` is append-only, so a game abandoned and then reopened still carries its
       `game_ended` for ever. Clearing only the pause left the page showing "abandoned" over a game
       that was playing — and it survived a refresh, because the stale ending was in the log rather
       than in any cache. */
    seq = 0;
    const events = [
      ...turn(1, "white", "e4"),
      event("game_ended", { result: "*", termination: "abandoned", detail: "provider 404" }),
      event("game_resumed", { detail: "reopened by an operator" }),
      ...turn(2, "black", "e5"),
    ];

    const { ended, moves } = foldEvents(events, []);

    expect(ended).toBeNull();
    expect(moves).toEqual(["e4", "e5"]);
  });

  it("and an ending after the resume still counts", () => {
    seq = 0;
    const events = [
      event("game_ended", { result: "*", termination: "abandoned", detail: "first" }),
      event("game_resumed", { detail: "reopened" }),
      event("game_ended", { result: "1-0", termination: "checkmate", detail: "White mates." }),
    ];

    const { ended } = foldEvents(events, []);

    expect(ended?.termination).toBe("checkmate");
  });
});

describe("withheld reasoning", () => {
  it("counts the tokens when the text is held back", () => {
    /* A game you are *playing* strips the reasoning text on the way out (invariant 8) and keeps
       the count. An absent `reasoning` with a count is "you may not read this yet", not "the model
       said nothing" — and the two looked identical on the page. */
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w" }),
      event("thinking", { tokens: 801 }),
      event("move_made", { ply: 1, colour: "white", san: "e4" }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].reasoning).toEqual([]);
    expect(turns[0].withheldReasoning).toBe(801);
  });

  it("stays zero when the text is published", () => {
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w" }),
      event("thinking", { tokens: 801, reasoning: "The Italian looks right." }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].reasoning).toEqual(["The Italian looks right."]);
    expect(turns[0].withheldReasoning).toBe(0);
  });

  it("stays zero for a model that does not reason at all", () => {
    /* Gemini says everything in `content` and emits no reasoning. A "thinking" badge on a model
       that never thinks aloud would be inventing a state. */
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w" }),
      event("output", { content: "I'll play e4." }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].withheldReasoning).toBe(0);
  });
});

describe("compactionText", () => {
  it("names a clamp rather than falling back to 'history compacted'", () => {
    /* Clamping is the one pass that shortens something the *model* wrote rather than dropping
       something a tool returned. A clamp-only pass has folded 0 and trimmed 0, so it used to hit
       the bare fallback — exactly the reading a person should not be left with when a reply has
       had its middle removed. */
    const text = compactionText({ folded: 0, trimmed: 0, clamped: 2 });

    expect(text).toContain("2 long replies shortened");
    expect(text).not.toContain("history compacted");
  });

  it("says reply, not replies, for one", () => {
    expect(compactionText({ folded: 0, trimmed: 0, clamped: 1 })).toContain("1 long reply shortened");
  });

  it("still describes a fold and a trim alongside it", () => {
    const text = compactionText({ folded: 40, trimmed: 3, clamped: 1 });

    expect(text).toContain("40 messages summarised");
    expect(text).toContain("3 stale tool results dropped");
    expect(text).toContain("1 long reply shortened");
  });

  it("falls back only when nothing at all was recorded", () => {
    expect(compactionText({})).toContain("history compacted");
  });
});

describe("a turn keeps the order it happened in", () => {
  /**
   * **The shape this exists for**, taken verbatim from `e601f9af` ply 8 on production: the model
   * reasons, calls a tool, reasons about what came back, calls another, tries an illegal move,
   * reasons about the refusal, writes prose, reasons once more, then moves.
   *
   * The log had all of that in `seq` order the whole time. `foldEvents` sorted it into four arrays
   * by kind and the panel drew them one after another — every thought, then all the prose, then
   * every tool call at the end — so a reader could not tell which reasoning discussed which tool
   * result. The interleaving was not missing; it was discarded on the way to the screen.
   */
  it("interleaves reasoning, tools and output as the model produced them", () => {
    seq = 0;
    const events = [
      event("turn_started", { ply: 8, colour: "black", player_id: "b", model: "m" }),
      event("thinking", { reasoning: "let me look at the board", tokens: 21 }),
      event("tool_called", { tool: "get_board", ok: true }),
      event("thinking", { reasoning: "now the history", tokens: 489 }),
      event("tool_called", { tool: "get_move_history", ok: true }),
      event("thinking", { reasoning: "e4 looks good", tokens: 8148 }),
      event("illegal_attempt", { move: "e4", detail: "no legal move matches", attempt: 1 }),
      event("thinking", { reasoning: "e4 is illegal because", tokens: 18687 }),
      event("output", { content: "The move e4 is illegal — the pawn already moved." }),
      event("thinking", { reasoning: "pick from the list", tokens: 81 }),
      event("tool_called", { tool: "make_move", ok: true, args: { move: "Bb4+" } }),
      event("move_made", { ply: 8, colour: "black", san: "Bb4+" }),
    ];

    const { turns } = foldEvents(events, []);

    expect(turns[0].blocks.map((b) => b.kind)).toEqual([
      "reasoning",
      "tool",
      "reasoning",
      "tool",
      "reasoning",
      "illegal",
      "reasoning",
      "output",
      "reasoning",
      "tool",
    ]);
  });

  it("orders blocks by seq, so a replayed event cannot shuffle them", () => {
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w", model: "m" }),
      event("thinking", { reasoning: "first", tokens: 1 }),
      event("tool_called", { tool: "get_board", ok: true }),
      event("thinking", { reasoning: "second", tokens: 2 }),
    ];

    const { turns } = foldEvents([...events].reverse(), []);
    const seqs = turns[0].blocks.map((b) => b.seq);

    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
  });

  it("carries a reasoning block's duration when the event records one", () => {
    /** Absent across the whole archive written before `duration_ms` existed, so null is the
        honest answer for those — "not recorded" must not render as "took no time". */
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w", model: "m" }),
      event("thinking", { reasoning: "timed", tokens: 10, duration_ms: 368707 }),
      event("thinking", { reasoning: "untimed", tokens: 10 }),
    ];

    const blocks = foldEvents(events, []).turns[0].blocks;

    expect(blocks[0]).toMatchObject({ kind: "reasoning", durationMs: 368707 });
    expect(blocks[1]).toMatchObject({ kind: "reasoning", durationMs: null });
  });

  it("keeps the by-kind arrays agreeing with the blocks", () => {
    /* The chips filter and the memo comparison read the arrays; the panel reads the blocks. Two
       views of one turn that can disagree is the bug this whole file exists to prevent. */
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w", model: "m" }),
      event("thinking", { reasoning: "a", tokens: 1 }),
      event("output", { content: "b" }),
      event("tool_called", { tool: "get_board", ok: true }),
      event("illegal_attempt", { move: "e9", detail: "no", attempt: 1 }),
    ];

    const turn = foldEvents(events, []).turns[0];
    const count = (kind: string) => turn.blocks.filter((b) => b.kind === kind).length;

    expect(count("reasoning")).toBe(turn.reasoning.length);
    expect(count("output")).toBe(turn.output.length);
    expect(count("tool")).toBe(turn.tools.length);
    expect(count("illegal")).toBe(turn.illegal.length);
  });

  it("does not build a block for reasoning whose text is withheld", () => {
    /* Invariant 8: a person playing this game gets the count and no text. A block with an empty
       body would render as the model having thought nothing, which is the opposite of true. */
    seq = 0;
    const events = [
      event("turn_started", { ply: 1, colour: "white", player_id: "w", model: "m" }),
      event("thinking", { tokens: 4096 }),
    ];

    const turn = foldEvents(events, []).turns[0];

    expect(turn.blocks).toEqual([]);
    expect(turn.withheldReasoning).toBe(4096);
  });
});

describe("live frames (ADR-0035)", () => {
  /**
   * A turn is one transaction, so its events do not exist until every round has finished. Ply 8
   * of `e601f9af` spent 632 seconds generating and then delivered all fifteen of its events in
   * the same millisecond. These arrive as each round lands.
   *
   * They are not the record and must never look like it: no `seq`, nothing stored, and the
   * committed events replace them.
   */
  const started: LiveFrame = {
    frame: "turn",
    player_id: "b",
    colour: "black",
    ply: 8,
    model: "m",
  };

  it("opens a provisional turn, because turn_started is inside the transaction too", () => {
    /* Without this the frames describe a turn nothing has announced — `turn_started` reaches a
       spectator only when the turn is over, which is exactly when the frames stop mattering. */
    const turn = liveTurn([started]);

    expect(turn).toMatchObject({ ply: 8, colour: "black", live: true, san: null });
  });

  it("has no turn at all until one is announced", () => {
    expect(liveTurn([{ frame: "token", player_id: "b", kind: "reasoning", text: "hm" }])).toBeNull();
  });

  it("builds the same blocks a committed turn would, in the same order", () => {
    const turn = liveTurn([
      started,
      { frame: "block", player_id: "b", kind: "reasoning", text: "look", tokens: 21 },
      { frame: "block", player_id: "b", kind: "tool", tool: "get_board", ok: true, args: {} },
      { frame: "block", player_id: "b", kind: "reasoning", text: "move", tokens: 81 },
      {
        frame: "block",
        player_id: "b",
        kind: "tool",
        tool: "make_move",
        ok: true,
        args: { move: "Bb4+" },
      },
    ]);

    expect(turn?.blocks.map((b) => b.kind)).toEqual(["reasoning", "tool", "reasoning", "tool"]);
  });

  it("shows the block still being generated, last", () => {
    /** The 369-second round, readable while it is happening rather than after. */
    const turn = liveTurn([
      started,
      { frame: "block", player_id: "b", kind: "reasoning", text: "done", tokens: 21 },
      { frame: "token", player_id: "b", kind: "reasoning", text: "the pawn " },
      { frame: "token", player_id: "b", kind: "reasoning", text: "on e2" },
    ]);

    expect(turn?.blocks.at(-1)).toMatchObject({
      kind: "reasoning",
      text: "the pawn on e2",
      // Neither is known until the round returns, and "reasoned for 0s" while it is still
      // reasoning would be worse than no label at all.
      tokens: 0,
      durationMs: null,
    });
  });

  it("replaces the fragments with the block they were previewing", () => {
    /* Otherwise the finished block renders *and* the fragments that predicted it, which is the
       same text twice with the second copy permanently incomplete. */
    const turn = liveTurn([
      started,
      { frame: "token", player_id: "b", kind: "reasoning", text: "the pawn " },
      { frame: "block", player_id: "b", kind: "reasoning", text: "the pawn on e2", tokens: 12 },
    ]);

    expect(turn?.blocks).toHaveLength(1);
    expect(turn?.blocks[0]).toMatchObject({ text: "the pawn on e2", tokens: 12 });
  });

  it("keys provisional blocks apart from committed ones", () => {
    /* React keys on `seq`, and a provisional block colliding with a real one would hand a
       prediction the DOM of a fact. Negative and descending cannot collide: `seq` is 1-based and
       gap-free per game (ADR-0008). */
    const turn = liveTurn([
      started,
      { frame: "block", player_id: "b", kind: "reasoning", text: "a", tokens: 1 },
      { frame: "block", player_id: "b", kind: "reasoning", text: "b", tokens: 1 },
    ]);
    const seqs = turn?.blocks.map((b) => b.seq) ?? [];

    expect(seqs.every((seq) => seq < 0)).toBe(true);
    expect(new Set(seqs).size).toBe(seqs.length);
  });

  it("carries the by-kind arrays a filter chip reads", () => {
    const turn = liveTurn([
      started,
      { frame: "block", player_id: "b", kind: "reasoning", text: "a", tokens: 1 },
      { frame: "block", player_id: "b", kind: "tool", tool: "get_board", ok: true, args: {} },
      { frame: "block", player_id: "b", kind: "said", text: "your move" },
    ]);

    expect(turn?.reasoning).toEqual(["a"]);
    expect(turn?.tools.map((t) => t.name)).toEqual(["get_board"]);
    expect(turn?.said).toEqual(["your move"]);
  });

  it("renders an illegal attempt from the frame the same way the event does", () => {
    const turn = liveTurn([
      started,
      {
        frame: "block",
        player_id: "b",
        kind: "illegal",
        tool: "make_move",
        ok: false,
        args: { move: "e4" },
        result: { detail: "no legal move matches" },
        attempt: 1,
      },
    ]);

    expect(turn?.blocks[0]).toEqual({
      kind: "illegal",
      seq: expect.any(Number),
      move: "e4",
      detail: "no legal move matches",
      attempt: 1,
    });
  });
});

describe("every live frame kind reaches the fold", () => {
  /**
   * **The bug this exists for.** `useGameStream` filtered incoming frames to `block` and `token`
   * and dropped `turn`, which was added later. `liveTurn` hangs everything off that frame and
   * returns null without it, so every frame arrived, was discarded, and the panel showed the turn
   * only once it committed — the feature invisible, and nothing erroring.
   *
   * Asserted as a property of the union rather than of one kind, so the next frame type added
   * cannot be forgotten in the same way.
   */
  it("accepts each kind the backend can send", () => {
    const kinds: LiveFrame[] = [
      { frame: "turn", player_id: "b", colour: "black", ply: 8, model: "m" },
      { frame: "block", player_id: "b", kind: "reasoning", text: "a", tokens: 1 },
      { frame: "block", player_id: "b", kind: "output", text: "b" },
      { frame: "block", player_id: "b", kind: "tool", tool: "get_board", ok: true, args: {} },
      { frame: "block", player_id: "b", kind: "said", text: "c" },
      { frame: "token", player_id: "b", kind: "reasoning", text: "d" },
    ];

    const turn = liveTurn(kinds);

    expect(turn).not.toBeNull();
    expect(turn?.blocks.map((b) => b.kind)).toEqual([
      "reasoning",
      "output",
      "tool",
      "said",
      "reasoning",
    ]);
  });
});

describe("a live block with nothing in it", () => {
  /**
   * A provisional block exists as soon as its first fragment arrives, so a model that opens its
   * reasoning with a newline produced an empty bordered box in the timeline. Drawn as a step it
   * reads as "the model wrote this: (nothing)" — a claim about the model rather than about a
   * frame that has not filled in yet.
   */
  const started: LiveFrame = {
    frame: "turn",
    player_id: "b",
    colour: "black",
    ply: 8,
    model: "m",
  };

  it("does not become a partial block on whitespace alone", () => {
    const turn = liveTurn([
      started,
      { frame: "token", player_id: "b", kind: "output", text: "\n" },
      { frame: "token", player_id: "b", kind: "output", text: "  " },
    ]);

    expect(turn?.blocks).toEqual([]);
  });

  it("appears as soon as there is something to read", () => {
    const turn = liveTurn([
      started,
      { frame: "token", player_id: "b", kind: "output", text: "\n" },
      { frame: "token", player_id: "b", kind: "output", text: "Playing e4." },
    ]);

    expect(turn?.blocks).toMatchObject([{ kind: "output", text: "\nPlaying e4." }]);
  });
});

describe("sameTurnContent", () => {
  /**
   * **The bug this exists for, and it is invisible by nature.** A memo that reports "unchanged"
   * too eagerly does not fail — it stops updating the screen while the data underneath goes on
   * changing. This compared `blocks.length` alone, and a block still being generated grows a
   * fragment at a time while the list does not: the first token created the block and every token
   * after it was dropped. The reasoning appeared with one word in it and froze; a refresh rebuilt
   * from the buffer and showed the lot, which is the tell that the data was right and the render
   * was skipped.
   */
  const base: LiveFrame = { frame: "turn", player_id: "b", colour: "black", ply: 8, model: "m" };

  const withText = (text: string) =>
    liveTurn([base, { frame: "token", player_id: "b", kind: "reasoning", text }])!;

  it("sees a block that grew without the list growing", () => {
    expect(sameTurnContent(withText("Let"), withText("Let me look at"))).toBe(false);
  });

  it("still calls two identical renderings the same", () => {
    // The property the memo exists for: scrubbing hands back new objects for unchanged turns, and
    // re-rendering every one of them was most of what a scrubber step cost.
    expect(sameTurnContent(withText("Let"), withText("Let"))).toBe(true);
  });

  it("sees a new block appended", () => {
    const one = liveTurn([base, { frame: "block", player_id: "b", kind: "reasoning", text: "a", tokens: 1 }])!;
    const two = liveTurn([
      base,
      { frame: "block", player_id: "b", kind: "reasoning", text: "a", tokens: 1 },
      { frame: "block", player_id: "b", kind: "tool", tool: "get_board", ok: true, args: {} },
    ])!;

    expect(sameTurnContent(one, two)).toBe(false);
  });

  it("sees the move that closes a turn", () => {
    const open = withText("thinking");
    expect(sameTurnContent(open, { ...open, san: "Bb4+", live: false })).toBe(false);
  });
});

/**
 * A game that ended says so, and says why (ADR-0031).
 *
 * Six games in `pool-free` were reported as "abandoned in the UI without stating the reason". Every
 * one of them had its reason stored correctly the whole time — in `termination_detail` and in the
 * `game_ended` payload — and two separate bugs kept it off the page:
 *
 * * a game abandoned **before its first move** folded an empty event list, so the replay rendered
 *   "The starting position — step forward to begin." over a pairing that never began and never
 *   would, with its pauses and its ending sitting in the log unread;
 * * `game_ended` was the one lifecycle event that pushed no notice, so a stream ran
 *   pause → resume → pause → resume and simply stopped. On a game reopened and then abandoned
 *   again, the last thing a reader saw was "resumed".
 */

import { describe, expect, it } from "vitest";

import { eventsThroughPly, plyCount } from "@/lib/replay";
import { foldEvents } from "@/lib/turns";
import type { GameEvent, EventType } from "@/lib/types";

let seq = 0;
function event(type: EventType, payload: Record<string, unknown> = {}): GameEvent {
  seq += 1;
  return { seq, type, payload };
}

/** The real shape of `cee1a24e`: started, paused on a hot pool four times, abandoned at ply 0. */
const REASON =
  "Abandoned after 27.0h and 4 pauses: google/gemma-4-26b-a4b-it:free rate-limited by " +
  "Google AI Studio (upstream_provider_shared_pool)";

const NEVER_MOVED: GameEvent[] = [
  event("game_started", {}),
  event("game_paused", { reason: "rate-limited by Google AI Studio", seconds: 60 }),
  event("game_resumed", { detail: "the wait is over" }),
  event("game_paused", { reason: "rate-limited by Google AI Studio", seconds: 1800 }),
  event("game_resumed", { detail: "the wait is over" }),
  event("game_ended", { result: "*", termination: "abandoned", detail: REASON }),
];

describe("a game that never moved", () => {
  it("has no plies, which is what made the replay open at zero", () => {
    expect(plyCount(NEVER_MOVED)).toBe(0);
  });

  it("keeps its whole log at ply 0", () => {
    /* `ply <= 0` returning nothing is right when there are later plies to scrub forward to. It is
       exactly wrong when there are none: the entire log *is* the tail. */
    expect(eventsThroughPly(NEVER_MOVED, 0)).toHaveLength(NEVER_MOVED.length);
  });

  it("reports how it ended, and why", () => {
    const { ended } = foldEvents(eventsThroughPly(NEVER_MOVED, 0), []);

    expect(ended).not.toBeNull();
    expect(ended?.termination).toBe("abandoned");
    expect(ended?.detail).toBe(REASON);
  });

  it("shows the pauses it spent waiting", () => {
    const { notices } = foldEvents(eventsThroughPly(NEVER_MOVED, 0), []);

    expect(notices.filter((notice) => notice.kind === "paused")).toHaveLength(2);
  });

  it("still shows nothing at ply 0 of a game that did move", () => {
    /* The guard on the guard. Ply 0 is the starting position of a real game and no thinking belongs
       to it yet — showing the first model's reasoning there would put a plan on screen before the
       position it reasons about. */
    const played: GameEvent[] = [
      event("game_started", {}),
      event("turn_started", { ply: 1, colour: "white" }),
      event("move_made", { ply: 1, colour: "white", san: "e4" }),
    ];

    expect(eventsThroughPly(played, 0)).toHaveLength(0);
  });
});

describe("the ending in the stream", () => {
  it("is a notice, so the timeline says what happened", () => {
    const { notices } = foldEvents(NEVER_MOVED, []);
    const ending = notices.filter((notice) => notice.kind === "ended");

    expect(ending).toHaveLength(1);
    expect(ending[0].text).toContain("abandoned");
    expect(ending[0].text).toContain(REASON);
  });

  it("is the last notice even when a resume follows an earlier ending", () => {
    /* `9b372624`: abandoned, reopened by an operator, abandoned again. The log is append-only, so
       it carries two `game_ended` rows with a `game_resumed` between them — and the reader was
       shown the resume as the final word. */
    const reopened: GameEvent[] = [
      ...NEVER_MOVED,
      event("game_resumed", { ply: 18, previous_termination: "abandoned" }),
      event("game_ended", { result: "*", termination: "abandoned", detail: "and again" }),
    ];

    const { notices, ended } = foldEvents(reopened, []);
    const last = notices[notices.length - 1];

    expect(last.kind).toBe("ended");
    expect(last.text).toContain("and again");
    expect(ended?.detail).toBe("and again");
  });

  it("does not claim an ending for a game still being played", () => {
    const live: GameEvent[] = [
      event("game_started", {}),
      event("game_paused", { reason: "rate-limited" }),
      event("game_resumed", { detail: "the wait is over" }),
    ];

    const { notices, ended } = foldEvents(live, []);

    expect(ended).toBeNull();
    expect(notices.some((notice) => notice.kind === "ended")).toBe(false);
  });
});

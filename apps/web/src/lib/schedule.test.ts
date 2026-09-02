import { describe, expect, it } from "vitest";

import { SCHEDULE_PAGE, schedulePage } from "@/lib/schedule";
import type { TournamentPairing } from "@/lib/types";

function pairing(round: number, id: string): TournamentPairing {
  return {
    id,
    round_number: round,
    white_key: "vendor/white",
    black_key: "vendor/black",
    white_score: null,
    state: "waiting",
    game_id: null,
    abandoned_reason: null,
  } as TournamentPairing;
}

/** `count` pairings spread over rounds of `per` — the shape a pool actually produces. */
function schedule(count: number, per = 3): TournamentPairing[] {
  return Array.from({ length: count }, (_, i) => pairing(Math.floor(i / per) + 1, `p${i}`));
}

describe("the schedule's first page", () => {
  it("shows the latest matches, not the oldest", () => {
    /* The whole reason the list is reversed. A pool that has played three hundred games would
       otherwise open on the ones it played last week. */
    const { rounds, shown, remaining } = schedulePage(schedule(30));

    expect(shown).toBe(SCHEDULE_PAGE);
    expect(remaining).toBe(20);
    expect(rounds[0].round).toBe(10);
    expect(rounds.at(-1)!.round).toBeLessThan(10);
  });

  it("groups what it shows into rounds, newest first", () => {
    const { rounds } = schedulePage(schedule(30));

    expect(rounds.map((r) => r.round)).toEqual([10, 9, 8, 7]);
    // Ten matches across four rounds of three: the last round on the page is a partial one.
    expect(rounds.map((r) => r.pairings.length)).toEqual([3, 3, 3, 1]);
  });

  it("splits a round across the boundary rather than dropping or duplicating it", () => {
    /* The fiddly part: the cut runs across *matches* and the display is grouped by *round*, so a
       round usually straddles the boundary. It must appear partly now and wholly later, with
       every match accounted for exactly once. */
    const all = schedule(30);
    const first = schedulePage(all, 10);
    const second = schedulePage(all, 20);

    const ids = (page: ReturnType<typeof schedulePage>) =>
      page.rounds.flatMap((r) => r.pairings.map((p) => p.id));

    expect(ids(first)).toEqual(ids(second).slice(0, 10));
    expect(new Set(ids(second)).size).toBe(20);
  });

  it("keeps the API's order inside a round", () => {
    /* Pairings within a round arrive in the order they were paired. Re-sorting them here would
       invent an ordering nothing promised. */
    const { rounds } = schedulePage(schedule(6), 6);

    expect(rounds[0].pairings.map((p) => p.id)).toEqual(["p3", "p4", "p5"]);
  });
});

describe("loading more", () => {
  it("runs out rather than over", () => {
    const { shown, remaining } = schedulePage(schedule(7), 10);

    expect(shown).toBe(7);
    expect(remaining).toBe(0);
  });

  it("reaches the end exactly, so the button disappears when it should", () => {
    /* An off-by-one here leaves a "load more" that loads nothing, which is worse than no button:
       it says there is something the reader has not seen. */
    const all = schedule(25);
    let visible = SCHEDULE_PAGE;
    let page = schedulePage(all, visible);
    while (page.remaining > 0) {
      visible += SCHEDULE_PAGE;
      page = schedulePage(all, visible);
    }

    expect(page.shown).toBe(25);
    expect(page.rounds.flatMap((r) => r.pairings)).toHaveLength(25);
  });

  it("an empty schedule shows nothing and offers nothing", () => {
    const { rounds, shown, remaining } = schedulePage([]);

    expect(rounds).toEqual([]);
    expect(shown).toBe(0);
    expect(remaining).toBe(0);
  });

  it("a nonsense page size is empty, never counted from the end of the list", () => {
    // `slice(0, -1)` would quietly return everything but the last match, which looks like data.
    expect(schedulePage(schedule(5), -1).shown).toBe(0);
  });
});

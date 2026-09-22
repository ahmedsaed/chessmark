/**
 * Which turn the lobby shows.
 *
 * Built out of real folded events rather than hand-written `TurnView` objects: the picker's whole
 * job is to read what `foldEvents` produced, and a fixture that invents that shape would go on
 * passing after the fold changed underneath it.
 */

import { describe, expect, it } from "vitest";

import { pickTurn, scoreTurn, thinkingIn } from "@/lib/spotlight";
import { foldEvents } from "@/lib/turns";
import type { EventType, GameEvent, TurnView } from "@/lib/types";

/** A thought long enough to clear `MIN_THINKING`, which short fixtures no longer do. */
function thought(about: string): string {
  return (
    `${about}. The knight on f3 is doing nothing where it stands and the light squares around ` +
    "the king are the ones I keep having to defend, so this is the moment to change that."
  );
}

let seq = 0;
function event(type: EventType, payload: Record<string, unknown> = {}): GameEvent {
  seq += 1;
  return { seq, type, payload };
}

interface TurnSpec {
  ply: number;
  colour: "white" | "black";
  san: string;
  thinking?: string;
  tool?: boolean;
  illegal?: string;
  human?: boolean;
}

function turn(spec: TurnSpec): GameEvent[] {
  const events = [
    event("turn_started", {
      ply: spec.ply,
      colour: spec.colour,
      player_id: spec.colour,
      model: spec.human ? null : `${spec.colour}-model`,
      human: spec.human === true,
    }),
  ];
  if (spec.thinking) events.push(event("thinking", { reasoning: spec.thinking }));
  if (spec.illegal) {
    events.push(
      event("illegal_attempt", { move: spec.illegal, detail: "not a legal move", attempt: 1 }),
    );
  }
  if (spec.tool) events.push(event("tool_called", { tool: "propose_move", ok: true }));
  events.push(event("move_made", { ply: spec.ply, colour: spec.colour, san: spec.san }));
  return events;
}

function turnsFrom(...specs: TurnSpec[]): TurnView[] {
  seq = 0;
  return foldEvents([event("game_started"), ...specs.flatMap(turn)], []).turns;
}

describe("pickTurn", () => {
  it("passes over a turn that published no thinking", () => {
    const turns = turnsFrom(
      { ply: 1, colour: "white", san: "e4", tool: true },
      { ply: 2, colour: "black", san: "e5", thinking: thought("symmetry is fine for now") },
    );

    expect(pickTurn(turns)?.ply).toBe(2);
  });

  it("prefers the turn where a model tried something illegal", () => {
    const turns = turnsFrom(
      { ply: 1, colour: "white", san: "e4", thinking: thought("take the centre"), tool: true },
      { ply: 2, colour: "black", san: "Nf6", thinking: thought("develop"), illegal: "Nd7" },
      { ply: 3, colour: "white", san: "Nc3", thinking: thought("also develop"), tool: true },
    );

    const picked = pickTurn(turns);
    expect(picked?.ply).toBe(2);
    expect(picked?.illegal[0].move).toBe("Nd7");
  });

  it("breaks a tie on the later ply, because every model thinks the same about e4", () => {
    const turns = turnsFrom(
      { ply: 1, colour: "white", san: "e4", thinking: thought("take the centre"), tool: true },
      { ply: 6, colour: "black", san: "Bb4", thinking: thought("take the centre"), tool: true },
    );

    expect(pickTurn(turns)?.ply).toBe(6);
  });

  it("refuses a fragment of the prompt template dressed up as a thought", () => {
    /* **What shipped for about ten minutes.** The turn this is built from is real: two tokens of
       "reasoning" whose entire content was `</role>`, picked because the rule was "non-empty" and
       rendered under the heading "inside one turn". */
    const turns = turnsFrom(
      { ply: 1, colour: "white", san: "e4", thinking: "</role>", tool: true },
      { ply: 2, colour: "black", san: "c5", thinking: "ok", tool: true },
    );

    expect(pickTurn(turns)).toBeNull();
  });

  it("prefers the model that said more, all else equal", () => {
    const turns = turnsFrom(
      { ply: 1, colour: "white", san: "e4", thinking: thought("brief"), tool: true },
      {
        ply: 2,
        colour: "black",
        san: "c5",
        thinking: `${thought("at length")} ${thought("and then some")}`,
        tool: true,
      },
    );

    expect(pickTurn(turns)?.ply).toBe(2);
  });

  it("has nothing to show when no model published any thinking", () => {
    const turns = turnsFrom(
      { ply: 1, colour: "white", san: "e4", tool: true },
      { ply: 2, colour: "black", san: "e5", tool: true },
    );

    expect(pickTurn(turns)).toBeNull();
  });

  it("skips a person's turn, which has no provider call behind it", () => {
    /* The thinking is there on purpose: without it the turn would be rejected for having nothing
       to show and this would pass with the `human` rule deleted. */
    const turns = turnsFrom({
      ply: 1,
      colour: "white",
      san: "e4",
      thinking: thought("a person would not publish this, but the rule must stand on its own"),
      human: true,
    });

    expect(turns[0].human, "the fixture should fold into a human turn").toBe(true);
    expect(pickTurn(turns)).toBeNull();
  });
});

describe("the score", () => {
  it("ranks an illegal attempt above a tool call, and a tool call above a bare move", () => {
    const [illegal, tooled, bare] = turnsFrom(
      { ply: 1, colour: "white", san: "e4", thinking: thought("a"), illegal: "Ke2" },
      { ply: 2, colour: "black", san: "e5", thinking: thought("b"), tool: true },
      { ply: 3, colour: "white", san: "Nf3", thinking: thought("c") },
    );

    expect(scoreTurn(illegal)).toBeGreaterThan(scoreTurn(tooled));
    expect(scoreTurn(tooled)).toBeGreaterThan(scoreTurn(bare));
  });
});

describe("thinkingIn", () => {
  it("is the first block with text in it, not the first block", () => {
    const [only] = turnsFrom({ ply: 1, colour: "white", san: "e4", thinking: "  the plan  " });

    expect(thinkingIn(only)).toBe("the plan");
  });
});

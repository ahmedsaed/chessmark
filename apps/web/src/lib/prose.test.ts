import { describe, expect, it } from "vitest";

import {
  gameSummary,
  illegalRateLabel,
  momentCaption,
  moveOf,
  plain,
  resultBarLabel,
} from "@/lib/prose";
import type { GameDetail, Moment, Player } from "@/lib/types";

/* Names the length production's are. A short fixture hides exactly the bugs width causes, and a
   sentence about two models reads very differently with these in it than with "a" and "b". */
const WHITE = "NVIDIA: Nemotron 3 Super 120B A12B (free)";
const BLACK = "Poolside: Laguna S 2.1 (free)";

function player(colour: "white" | "black", over: Partial<Player> = {}): Player {
  return {
    id: colour,
    colour,
    kind: "model",
    display_name: colour === "white" ? WHITE : BLACK,
    model: colour === "white" ? "nvidia/nemotron-3-super:free" : "poolside/laguna-s-2.1:free",
    runtime: "llm",
    provider_routing: {},
    pinned_provider: null,
    providers_used: [],
    quantization: null,
    illegal_attempts: 0,
    compactions: 0,
    forfeited: false,
    prompt_tokens: 0,
    completion_tokens: 0,
    reasoning_tokens: 0,
    cached_tokens: 0,
    total_cost_usd: "0",
    ...over,
  };
}

function game(over: Partial<GameDetail> = {}): GameDetail {
  return {
    id: "g",
    status: "finished",
    result: "1-0",
    termination: "checkmate",
    winner_colour: "white",
    ply_count: 41,
    is_ranked: true,
    trash_talk_enabled: false,
    total_cost_usd: "0.0123",
    total_tokens: 48210,
    created_at: "2026-10-01T00:00:00Z",
    started_at: null,
    ended_at: null,
    pause_reason: null,
    resume_after: null,
    players: [player("white"), player("black")],
    waiting_on: null,
    start_fen: "",
    current_fen: "",
    termination_detail: null,
    prompt_version: null,
    tool_schema_version: null,
    decision_version: null,
    max_usd: null,
    max_illegal_retries: 5,
    max_plies: 300,
    provider_routing: {},
    event_seq: 0,
    moves: ["e4", "e5", "Qh5#"],
    tournament: null,
    digest: {
      version: 1,
      opening: { eco: "C20", name: "King's Pawn Game", ply: 2 },
      material: { white: 39, black: 30 },
      moments: [],
    },
    ...over,
  };
}

const text = (g: GameDetail) => gameSummary(g).map(plain).join(" ");

describe("moveOf", () => {
  it("counts plies 1 and 2 as move 1", () => {
    expect([1, 2, 3, 41].map(moveOf)).toEqual([1, 1, 2, 21]);
  });
});

describe("gameSummary", () => {
  it("names both seats, the opening, the ending, the material and the cost", () => {
    const said = text(game());
    expect(said).toContain(`${WHITE} had White and ${BLACK} had Black.`);
    expect(said).toContain("The opening was the King's Pawn Game (C20), reached on move 1.");
    expect(said).toContain(`${WHITE} won by checkmate on move 21, with Qh5#.`);
    expect(said).toContain("White finished 9 points of material up, 39 to 30.");
    expect(said).toContain("Neither model attempted an illegal move.");
    expect(said).toContain("The model calls cost $0.0123, 48,210 tokens in all.");
  });

  it("links each model to its page and never links a person", () => {
    const g = game({ players: [player("white"), player("black", { kind: "human", model: null })] });
    const links = gameSummary(g)
      .flat()
      .filter((part) => typeof part !== "string");
    expect(links).toContainEqual({ text: WHITE, href: "/models/nvidia/nemotron-3-super:free" });
    expect(links.some((link) => link.text === BLACK)).toBe(false);
  });

  it("names the event and its round", () => {
    const g = game({
      tournament: {
        slug: "pool-free",
        name: "Free Models",
        format: "pool",
        round_number: 261,
        era: null,
      },
    });
    expect(text(g)).toContain("had Black, in round 261 of Free Models.");
  });

  it("says who resigned, not who won", () => {
    const g = game({ termination: "resignation", winner_colour: "white", ply_count: 30 });
    expect(text(g)).toContain(`${BLACK} resigned on move 16.`);
  });

  /* Production game 748cf5ef: 28 plies, then White's turn forfeited. The summary said move 14 and
     the quoted last turn, from the turn's own ply, said move 15. */
  it("puts a forfeit on the turn it happened, after the last ply", () => {
    const g = game({ termination: "illegal_move_forfeit", winner_colour: "black", ply_count: 28 });
    expect(text(g)).toContain(`${WHITE} forfeited on move 15, after 5 illegal attempts in a row.`);
  });

  it("quotes the harness's reason for an error forfeit as a sentence", () => {
    const g = game({
      termination: "error_forfeit",
      winner_colour: "black",
      termination_detail: "White replied without calling a tool 4 times in a row",
    });
    expect(text(g)).toContain(
      `${WHITE} forfeited on move 21: White replied without calling a tool 4 times in a row.`,
    );
  });

  it("says a draw is a draw and names the rule", () => {
    const g = game({ termination: "threefold_repetition", winner_colour: null, result: "1/2-1/2" });
    expect(text(g)).toContain("The game was drawn by threefold repetition on move 21.");
  });

  /* Invariant 11: a bound of ours is never a finding about a player. A ply cap must not read as
     either model having done anything. */
  it("calls a harness stop a stop, and names no player", () => {
    const g = game({ termination: "ply_cap", winner_colour: null, result: "1/2-1/2" });
    const ending = gameSummary(g).map(plain)[2];
    expect(ending).toBe("The game was stopped on move 21: move cap.");
  });

  it("counts illegal attempts White first, and says the other made none", () => {
    const g = game({
      players: [player("black", { illegal_attempts: 3 }), player("white")],
    });
    expect(text(g)).toContain(`${BLACK} made 3 illegal move attempts; its opponent made none.`);
  });

  it("says level material as level", () => {
    const g = game({
      digest: { version: 1, opening: null, material: { white: 12, black: 12 }, moments: [] },
    });
    expect(text(g)).toContain("Material was level at the end, 12 points each.");
    expect(text(g)).not.toContain("opening");
  });
});

describe("momentCaption", () => {
  const moment = (over: Partial<Moment>): Moment => ({
    kind: "final",
    ply: 41,
    colour: "white",
    san: "Qh5#",
    excerpt: "Mate.",
    source: "reasoning",
    truncated: false,
    ...over,
  });

  it("says what an illegal attempt tried, why it was refused, and what was played", () => {
    const said = plain(
      momentCaption(
        game(),
        moment({
          kind: "illegal",
          ply: 12,
          colour: "black",
          attempted: "Qxe4",
          detail: "the queen cannot reach e4",
          san: "Nf6",
        }),
      ),
    );
    expect(said).toBe(
      `${BLACK} tried Qxe4 on move 6, which was illegal: the queen cannot reach e4. ` +
        "It played Nf6 instead.",
    );
  });

  it("names the piece a capture took", () => {
    const said = plain(
      momentCaption(game(), moment({ kind: "capture", ply: 22, captured: "queen", san: "Bxd8" })),
    );
    expect(said).toBe(`${WHITE} took a queen with Bxd8 on move 11.`);
  });

  it("says a last turn without a move ended the game without one", () => {
    const said = plain(momentCaption(game(), moment({ san: null, colour: "black", ply: 42 })));
    expect(said).toBe(`${BLACK}'s last turn on move 21 ended the game without a move.`);
  });
});

describe("the model page's bar labels", () => {
  it("says what a result bar draws, in words a screen reader can read", () => {
    expect(resultBarLabel("As White", { wins: 3, draws: 1, losses: 0 })).toBe(
      "As White: 4 games, 3 wins, 1 draw and 0 losses.",
    );
    expect(resultBarLabel("Checkmate", { wins: 0, draws: 0, losses: 1 })).toBe(
      "Checkmate: 1 game, 0 wins, 0 draws and 1 loss.",
    );
  });

  it("compares the illegal-move rate with the field's", () => {
    expect(illegalRateLabel(1.952, 0.175)).toBe(
      "195.2 illegal move attempts per 100 moves played, against 17.5 across every model here.",
    );
  });
});

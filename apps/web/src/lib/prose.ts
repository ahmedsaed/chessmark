/**
 * The sentences a game page says about itself, and the accessible names of a model page's bars
 * (ADR-0069).
 *
 * **Why these exist.** A game page's readable text was its move list and two model ids, and to a
 * search engine four hundred such pages were one page. These turn the facts the API stores into
 * plain sentences that are true of *this* game and no other. A model page shows its facts as
 * tables instead — a paragraph of numbers is the wrong form for a reader comparing them — and only
 * its bars, which are pictures, borrow a sentence from here to be read aloud.
 *
 * **Wording lives here, facts live in the API.** The digest is stored and versioned by what it
 * computes; how it is phrased can change freely without recomputing anything.
 *
 * **Every claim is read off a field.** No sentence infers what a field does not say: a draw by
 * repetition is not "a hard-fought draw", and the biggest capture is not "the blunder" — nothing
 * has judged it one. Pure, so each sentence is tested against the data it describes.
 */

import { endingLabel } from "@/lib/archive";
import { usd } from "@/lib/credit";
import type { Colour, GameDetail, Moment, Player, RecordCount } from "@/lib/types";

/** A run of text, or a link inside it. A sentence is a list of these. */
export type Part = string | { text: string; href: string };
export type Sentence = Part[];

export function plain(sentence: Sentence): string {
  return sentence.map((part) => (typeof part === "string" ? part : part.text)).join("");
}

/** Move number from ply: plies 1 and 2 are move 1. */
export function moveOf(ply: number): number {
  return Math.ceil(ply / 2);
}

function count(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** A detail from the referee or the harness, ended as a sentence whether or not it was one. */
function stop(text: string): string {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

/** `a`, `a and b`, `a, b and c` — over parts, so a name inside can stay a link. */
function list(items: Part[][]): Sentence {
  return items.flatMap((item, index) => [
    ...(index === 0 ? [] : [index === items.length - 1 ? " and " : ", "]),
    ...item,
  ]);
}

// ====================================================================== the game page

function seat(game: GameDetail, colour: Colour): Player | undefined {
  return game.players.find((player) => player.colour === colour);
}

/** A seat's name, linked to its model page when it is a model. People are never linked. */
function who(game: GameDetail, colour: Colour): Part {
  const player = seat(game, colour);
  const text = player?.display_name ?? (colour === "white" ? "White" : "Black");
  if (player?.kind !== "model" || !player.model) return text;
  return { text, href: `/models/${player.model}` };
}

const DRAWS = new Set([
  "stalemate",
  "threefold_repetition",
  "fivefold_repetition",
  "fifty_move_rule",
  "seventy_five_move_rule",
  "insufficient_material",
  "agreed_draw",
]);

/** How the game ended, as one sentence. */
function ending(game: GameDetail): Sentence | null {
  const { termination, winner_colour: winner } = game;
  if (!termination) return null;
  const on = `on move ${moveOf(game.ply_count)}`;
  /* A resignation or a forfeit happens on the loser's turn, after the last ply, and played no
     move. Counted from the last ply it lands a move early whenever White was the one to go — and
     disagrees with the quoted last turn beside it. */
  const turn = `on move ${moveOf(game.ply_count + 1)}`;
  const loser: Colour | null = winner === null ? null : winner === "white" ? "black" : "white";
  const last = game.moves.at(-1);

  if (termination === "checkmate" && winner) {
    return [who(game, winner), ` won by checkmate ${on}${last ? `, with ${last}` : ""}.`];
  }
  if (termination === "resignation" && loser) {
    return [who(game, loser), ` resigned ${turn}.`];
  }
  if (termination === "illegal_move_forfeit" && loser) {
    const tries = game.max_illegal_retries;
    return [who(game, loser), ` forfeited ${turn}, after ${tries} illegal attempts in a row.`];
  }
  if (termination === "error_forfeit" && loser) {
    const detail = game.termination_detail?.trim();
    return [who(game, loser), ` forfeited ${turn}${detail ? `: ${stop(detail)}` : "."}`];
  }
  if (DRAWS.has(termination)) {
    return [`The game was drawn by ${endingLabel(termination)} ${on}.`];
  }
  /* A harness stop: a budget, a cap, an outage. Said as a stop and not as a result about either
     player, because a bound of ours is never a finding about a model (invariant 11). */
  return [`The game was stopped ${on}: ${endingLabel(termination)}.`];
}

function material(game: GameDetail): Sentence | null {
  const points = game.digest?.material;
  if (!points || game.ply_count === 0) return null;
  const gap = points.white - points.black;
  if (gap === 0) return [`Material was level at the end, ${points.white} points each.`];
  const ahead = gap > 0 ? "White" : "Black";
  const [more, less] = gap > 0 ? [points.white, points.black] : [points.black, points.white];
  return [`${ahead} finished ${count(Math.abs(gap), "point")} of material up, ${more} to ${less}.`];
}

function illegal(game: GameDetail): Sentence | null {
  const models = game.players.filter((player) => player.kind === "model");
  if (models.length === 0) return null;
  const tried = models
    .filter((player) => player.illegal_attempts > 0)
    .sort((a, b) => (a.colour === "white" ? -1 : 1) - (b.colour === "white" ? -1 : 1));
  if (tried.length === 0) {
    return [
      models.length === 2
        ? "Neither model attempted an illegal move."
        : "The model attempted no illegal move.",
    ];
  }
  const each = tried.map((player) => [
    who(game, player.colour),
    ` made ${count(player.illegal_attempts, "illegal move attempt")}`,
  ]);
  const other = tried.length === 1 && models.length === 2 ? "; its opponent made none." : ".";
  return [...list(each), other];
}

/** The paragraph above a finished game: who played, the opening, the ending, and what it cost. */
export function gameSummary(game: GameDetail): Sentence[] {
  const event = game.tournament;
  const round = event?.round_number ? `round ${event.round_number} of ` : "";
  const sentences: (Sentence | null)[] = [
    [
      who(game, "white"),
      " had White and ",
      who(game, "black"),
      " had Black",
      ...(event ? [`, in ${round}`, { text: event.name, href: `/tournaments/${event.slug}` }] : []),
      ".",
    ],
  ];

  const opening = game.digest?.opening;
  if (opening) {
    const reached = `reached on move ${moveOf(opening.ply)}`;
    sentences.push([`The opening was the ${opening.name} (${opening.eco}), ${reached}.`]);
  }

  sentences.push(ending(game), material(game), illegal(game));

  if (Number(game.total_cost_usd) > 0) {
    const tokens = game.total_tokens.toLocaleString("en-US");
    sentences.push([`The model calls cost ${usd(game.total_cost_usd)}, ${tokens} tokens in all.`]);
  }

  return sentences.filter((sentence): sentence is Sentence => sentence !== null);
}

/** The heading of a quoted moment. */
export function momentTitle(moment: Moment): string {
  const titles = {
    illegal: "The first illegal move",
    capture: "The biggest capture",
    final: "The end",
  };
  return titles[moment.kind];
}

/** What a quoted moment is, as the line under its heading. */
export function momentCaption(game: GameDetail, moment: Moment): Sentence {
  const name: Part = moment.colour ? who(game, moment.colour) : "A player";
  const on = moment.ply ? ` on move ${moveOf(moment.ply)}` : "";

  if (moment.kind === "illegal") {
    const detail = moment.detail?.trim();
    return [
      name,
      ` tried ${moment.attempted ?? "a move"}${on}, which was illegal`,
      detail ? `: ${stop(detail)}` : ".",
      ...(moment.san ? [` It played ${moment.san} instead.`] : []),
    ];
  }
  if (moment.kind === "capture") {
    return [name, ` took a ${moment.captured ?? "piece"} with ${moment.san}${on}.`];
  }
  return moment.san
    ? [name, ` played the last move of the game, ${moment.san}${on}.`]
    : [name, `'s last turn${on} ended the game without a move.`];
}

// ====================================================================== the model page

/* The model page shows its profile as tables and bars, not paragraphs: a paragraph of numbers
   is the wrong form for a reader comparing them. A bar is a picture, though, and a screen reader
   cannot read one, so each bar's accessible name is the sentence below. One sentence per bar, and
   only for the bars: the tables beside them are already text. */

/** Games in a record. */
export function games({ wins, draws, losses }: RecordCount): number {
  return wins + draws + losses;
}

/** "3 wins, 1 draw and 0 losses" */
export function recordWords({ wins, draws, losses }: RecordCount): string {
  return `${count(wins, "win")}, ${count(draws, "draw")} and ${count(losses, "loss", "losses")}`;
}

/** The accessible name of one result bar: what it is about, then the record it draws. */
export function resultBarLabel(subject: string, record: RecordCount): string {
  return `${subject}: ${count(games(record), "game")}, ${recordWords(record)}.`;
}

/** The accessible name of the illegal-move comparison. */
export function illegalRateLabel(mine: number, field: number): string {
  const per100 = (value: number) => (value * 100).toFixed(1);
  return (
    `${per100(mine)} illegal move attempts per 100 moves played, ` +
    `against ${per100(field)} across every model here.`
  );
}

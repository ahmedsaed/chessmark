/**
 * Which turn to put on the front page.
 *
 * The lobby claims every request, reasoning trace and tool call is recorded, and then shows boards
 * and numbers. One real turn — what the model thought, what it asked for, what it got wrong, what
 * it played — is that claim demonstrated rather than asserted.
 *
 * Pure and here rather than in the component, because "which turn is worth showing" has edge cases
 * worth asserting and a component is only ever checked by hand.
 */

import type { TurnBlock, TurnView } from "@/lib/types";

/**
 * How interesting a turn is to a reader who has never seen one.
 *
 * An illegal attempt is worth the most: it is the benchmark's headline number happening, and it
 * shows the harness answering — the model proposed a move, the referee refused it, and the model
 * tried again. A tool call is next, because it is the part people do not expect (a model does not
 * "say" a move here, it calls `propose_move`). The move itself is worth least: every turn has one.
 */
const ILLEGAL = 4;
const TOOL = 2;
const MOVED = 1;

/**
 * The shortest thing that counts as a thought.
 *
 * **Because "non-empty" put `</role>` on the front page.** The first build of this picked a turn
 * whose entire published reasoning was a stray closing tag — two tokens, `reasoned for 1s`, and a
 * section whose whole purpose is to show a model thinking, showing a model emitting a fragment of
 * its own prompt template. Providers leak these, and a rule that accepts any non-empty string
 * accepts them all.
 *
 * 120 characters is about a sentence and a half: long enough that a stray tag or a one-word answer
 * cannot clear it, short enough to keep a terse-but-real thought.
 */
const MIN_THINKING = 120;

function reasoningText(block: TurnBlock): string {
  return block.kind === "reasoning" ? block.text.trim() : "";
}

/** The first thing the model thought, if it published any thinking at all. */
export function thinkingIn(turn: TurnView): string {
  return turn.blocks.map(reasoningText).find((text) => text.length > 0) ?? "";
}

export function scoreTurn(turn: TurnView): number {
  return (
    (turn.illegal.length > 0 ? ILLEGAL : 0) +
    (turn.tools.length > 0 ? TOOL : 0) +
    (turn.san ? MOVED : 0)
  );
}

/**
 * The turn to show, or `null` when none of them has anything to say.
 *
 * **Real reasoning text is the entry requirement, not part of the score.** A turn with no
 * published thinking is a tool call and a move — true, dull, and not what the section is for.
 * Plenty of models never emit any (`TurnView.reasoning`: "DeepSeek fills this; Gemini never
 * does"), and a game between two of them yields nothing here. That is why this returns `null`
 * rather than a best-of-a-bad-lot, and why the lobby asks more than one game.
 *
 * A person's turn is skipped: there is no provider call behind it, so there is nothing to show.
 *
 * Ties go to the **longer thought**, then to the later ply. A turn from the middle of the opening
 * reads better than White's first move, where every model in the catalogue thinks the same three
 * things about `e4`.
 */
export function pickTurn(turns: TurnView[]): TurnView | null {
  const candidates = turns
    .filter((turn) => !turn.human && !turn.live)
    .filter((turn) => thinkingIn(turn).length >= MIN_THINKING);

  if (candidates.length === 0) return null;

  return candidates.reduce((best, turn) => (ranks(turn, best) ? turn : best));
}

/** Strictly better than the incumbent: score, then how much it said, then how late it said it. */
function ranks(turn: TurnView, best: TurnView): boolean {
  const score = scoreTurn(turn) - scoreTurn(best);
  if (score !== 0) return score > 0;

  const said = thinkingIn(turn).length - thinkingIn(best).length;
  if (said !== 0) return said > 0;

  return turn.ply > best.ply;
}

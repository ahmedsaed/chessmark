/**
 * One real turn, on the front page.
 *
 * **The page's central claim, demonstrated instead of asserted.** The hero says every request,
 * reasoning trace and tool call is stored and replayable, and then the whole lobby is boards and
 * numbers: a visitor never sees a model *think*. This is the thing nothing else on the site shows
 * above the fold — the deliberation, the tool call, the move the referee refused, and the one it
 * took instead.
 *
 * Nothing here is written by hand. It is a slice of a finished game's event log, folded by
 * `foldEvents` — the same function the game page's conversation is built from, so this cannot
 * drift into showing a turn in a shape the real view never produces.
 *
 * **Only a settled game between two models.** Reasoning is withheld from any game a person is
 * playing until it ends (invariant 8), so this would be empty rather than leaky — but the choice
 * is made on the way in, in `page.tsx`, rather than relied upon here.
 */

import Link from "next/link";

import { reasoningLabel } from "@/lib/turns";
import type { GameSummary, TurnBlock, TurnView } from "@/lib/types";

/** Enough of the model's thinking to show what thinking looks like; the game page has the rest. */
const LINES = "line-clamp-2";

/**
 * How many steps of the turn to draw.
 *
 * **An excerpt, not a transcript.** Drawing the whole turn put two reasoning blocks, a tool call
 * and 1.3k tokens of deliberation on the front page — honest, and half a screen of it. Four steps
 * is enough to show the shape: it thought, it asked the board something, it was refused, it moved.
 * The count of what is left is printed rather than dropped, because "and eleven more steps" is
 * itself the point being made.
 */
const STEPS = 4;

export function TurnSpotlight({ game, turn }: { game: GameSummary; turn: TurnView }) {
  const seat = game.players.find((player) => player.colour === turn.colour);
  const move = Math.ceil(turn.ply / 2);
  const drawable = turn.blocks.filter(renderable);
  const shown = drawable.slice(0, STEPS);
  const hidden = drawable.length - shown.length;

  return (
    <section className="mt-14">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Inside one turn
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <Link
          href={`/games/${game.id}`}
          prefetch={false}
          className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
        >
          Open the game →
        </Link>
      </div>

      <div className="border border-line bg-surface-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line-soft px-4 py-2.5">
          <span className="min-w-0 truncate font-mono text-data text-ink">
            {seat?.display_name ?? turn.model}
          </span>
          <span className="tabular font-mono text-meta text-ink-faint">
            move {move} · {turn.colour}
          </span>
        </div>

        <ol className="flex flex-col gap-3 px-4 py-4">
          {shown.map((block) => (
            <Step key={`${block.kind}-${block.seq}`} block={block} />
          ))}
          {hidden > 0 && (
            <li className="font-mono text-meta text-ink-faint">
              … {hidden} more step{hidden === 1 ? "" : "s"} in this turn
            </li>
          )}
          {turn.san && (
            <li className="flex items-baseline gap-2 font-mono text-data">
              <span className="text-good" aria-hidden>
                ✓
              </span>
              <span className="text-ink">
                played <span className="text-accent">{turn.san}</span>
              </span>
            </li>
          )}
        </ol>
      </div>

      <p className="mt-2 font-mono text-meta text-ink-faint">
        One turn of one game. Every turn of every game is recorded like this — the raw request and
        response included.
      </p>
    </section>
  );
}

/**
 * One step of the turn, in the order it happened.
 *
 * The four kinds that appear in an opening turn get a rendering; anything else — a pause, a taunt
 * — is dropped rather than half-drawn, because this is an excerpt and the game page is where the
 * complete record lives.
 */
/** The kinds `Step` draws. A pause or a taunt is dropped rather than half-rendered. */
function renderable(block: TurnBlock): boolean {
  return (
    block.kind === "reasoning" ||
    block.kind === "tool" ||
    block.kind === "illegal" ||
    block.kind === "output"
  );
}

function Step({ block }: { block: TurnBlock }) {
  if (block.kind === "reasoning") {
    return (
      <li className="flex flex-col gap-1.5">
        <span className="font-mono text-label uppercase tracking-[0.14em] text-machine">
          {reasoningLabel(block)}
        </span>
        {/* The machine colour, because this is the agent talking to itself rather than to anyone
            (ADR-0013). Clamped: some of these run to two thousand words. */}
        <p className={`${LINES} whitespace-pre-line text-sm leading-relaxed text-ink-dim`}>
          {block.text.trim()}
        </p>
      </li>
    );
  }

  if (block.kind === "tool") {
    return (
      <li className="flex flex-wrap items-baseline gap-2 font-mono text-data">
        <span className="text-ink-faint" aria-hidden>
          →
        </span>
        <span className="border border-machine-deep px-1.5 py-px text-machine">
          {block.call.name}
        </span>
        <span className="min-w-0 truncate text-ink-faint">{args(block.call.args)}</span>
      </li>
    );
  }

  if (block.kind === "illegal") {
    return (
      <li className="flex flex-wrap items-baseline gap-2 font-mono text-data text-bad">
        <span aria-hidden>✗</span>
        <span>{block.move}</span>
        <span className="min-w-0 text-ink-faint">— {block.detail}</span>
      </li>
    );
  }

  if (block.kind === "output") {
    return (
      <li className={`${LINES} whitespace-pre-line text-sm leading-relaxed text-ink-dim`}>
        {block.text.trim()}
      </li>
    );
  }

  return null;
}

/**
 * A tool's arguments as one short line.
 *
 * `JSON.stringify` of `{ san: "Nd7" }` is already the clearest form there is, and the tools take
 * one or two scalar arguments — so this is a format, not a renderer. Long values are the game
 * page's problem.
 */
function args(values: Record<string, unknown>): string {
  const pairs = Object.entries(values).map(([key, value]) => `${key}: ${JSON.stringify(value)}`);
  return pairs.length > 0 ? `{ ${pairs.join(", ")} }` : "";
}
